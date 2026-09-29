"""Restart recovery.

Brings every record back to a consistent, resumable state without redoing
completed work:
* RUNNING jobs → INTERRUPTED
* images caught mid-generation → PENDING (never marked complete)
* images caught mid-validation → GENERATED (validation re-runs)
* sources registered without features → feature extraction re-queued
* products mid-analysis/naming → intake re-queued
* products with outstanding generation → generation re-queued (live mode only)
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..config import settings
from ..database import session_scope
from ..models import (GeneratedImage, GenerationAttempt, GenStatus, Job, JobStatus, Product, ProductStatus,
                      SourceImage, SourceStatus, utcnow)
from .audit_logger import audit
from .job_manager import JobManager

log = logging.getLogger("jimiki.recovery")


def recover(manager: JobManager) -> dict[str, int]:
    stats = {"jobs_interrupted": 0, "images_reset": 0, "sources_requeued": 0, "products_requeued": 0}
    intake: list[int] = []
    generation: list[int] = []
    with session_scope() as s:
        for job in s.scalars(select(Job).where(Job.status.in_([JobStatus.RUNNING, JobStatus.QUEUED]))):
            job.status, job.completed_at = JobStatus.INTERRUPTED, utcnow()
            job.error_message = "Interrupted by application restart"
            stats["jobs_interrupted"] += 1
        for att in s.scalars(select(GenerationAttempt).where(GenerationAttempt.status == "RUNNING")):
            att.status, att.finished_at, att.error = "ERROR", utcnow(), "Interrupted by application restart"
        for gi in s.scalars(select(GeneratedImage).where(GeneratedImage.status.in_([GenStatus.GENERATING,
                                                                                     GenStatus.VALIDATING]))):
            gi.status = GenStatus.PENDING if gi.status == GenStatus.GENERATING else GenStatus.GENERATED
            stats["images_reset"] += 1
        for src in s.scalars(select(SourceImage).where(SourceImage.status == SourceStatus.REGISTERED)):
            manager.enqueue_features(src.id)
            stats["sources_requeued"] += 1
        for prod in s.scalars(select(Product).order_by(Product.product_number)):
            if prod.status in (ProductStatus.ANALYZING, ProductStatus.NAMING, ProductStatus.DISCOVERED,
                               ProductStatus.GROUPING):
                intake.append(prod.id)
            elif prod.on_hold:
                continue
            elif prod.status in (ProductStatus.QUEUED, ProductStatus.GENERATING_WHITE, ProductStatus.GENERATING_MODEL,
                                 ProductStatus.GENERATING_CLOSEUP, ProductStatus.VALIDATING):
                if prod.status != ProductStatus.QUEUED and not settings.generation_enabled:
                    prod.status = ProductStatus.QUEUED
                generation.append(prod.id)
        if any(stats.values()) or intake or generation:
            audit(s, "recovery", f"Startup recovery: {stats['jobs_interrupted']} interrupted job(s), "
                  f"{stats['images_reset']} image(s) reset, {len(intake) + len(generation)} product(s) resumed",
                  details=stats, level="WARN" if stats["jobs_interrupted"] else "INFO")
    for pk in intake:
        manager.enqueue_intake_product(pk)
    if settings.generation_enabled:
        for pk in generation:
            manager.enqueue_generation(pk)
    stats["products_requeued"] = len(intake) + len(generation)
    return stats


def requeue_waiting(manager: JobManager) -> int:
    """Called when generation becomes enabled (mode switched to live / key added)."""
    if not settings.generation_enabled:
        return 0
    n = 0
    with session_scope() as s:
        for prod in s.scalars(select(Product).where(Product.status == ProductStatus.QUEUED, Product.on_hold.is_(False))):
            manager.enqueue_generation(prod.id)
            n += 1
    return n
