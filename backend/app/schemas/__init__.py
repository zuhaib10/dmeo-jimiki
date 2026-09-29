"""Request schemas (Pydantic) and response serializers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from ..models import (AuditEvent, GeneratedImage, GenerationAttempt, ImageType, Job, Product, ProductAnalysis,
                      SourceImage, ValidationResult)
from ..services.workflow import progress_for, stages_for, state_label


# ---------------------------------------------------------------- requests
class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class ReviewAction(BaseModel):
    note: Optional[str] = None


# ---------------------------------------------------------------- helpers
def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def source_url(src: SourceImage, size: int | None = None) -> str:
    return f"/api/media/source/{src.id}" + (f"?size={size}" if size else "")


def generated_url(gi: GeneratedImage, fmt: str = "png", size: int | None = None) -> str | None:
    if not gi.png_path:
        return None
    v = int(gi.updated_at.timestamp()) if gi.updated_at else 0
    return f"/api/media/generated/{gi.id}/{fmt}?v={v}" + (f"&size={size}" if size else "")


# ---------------------------------------------------------------- serializers
def ser_source(src: SourceImage) -> dict[str, Any]:
    return {
        "id": src.id, "product_id": src.product_id, "filename": src.original_filename,
        "original_path": src.original_path, "current_path": src.current_path,
        "file_hash": src.file_hash, "perceptual_hash": src.perceptual_hash, "format": src.file_format,
        "mime_type": src.mime_type, "width": src.width, "height": src.height, "file_size": src.file_size,
        "status": src.status, "grouping_confidence": src.grouping_confidence, "grouping_reason": src.grouping_reason,
        "duplicate_of_id": src.duplicate_of_id, "exists": Path(src.current_path).exists(),
        "colours": [c["name"] for c in (src.features or {}).get("dominant", [])[:3]],
        "thumb_url": source_url(src, 360), "full_url": source_url(src, 1600),
        "created_at": iso(src.created_at), "archived_at": iso(src.archived_at), "error": src.error_message,
    }


def ser_attempt(a: GenerationAttempt) -> dict[str, Any]:
    return {"attempt": a.attempt_number, "status": a.status, "started_at": iso(a.started_at),
            "finished_at": iso(a.finished_at), "error": a.error, "metadata": a.provider_metadata}


def ser_validation(v: ValidationResult | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {"id": v.id, "result": v.result, "confidence": v.confidence, "flags": v.flags or [],
            "checks": v.checks or {}, "ai": v.ai_assessment, "attempt": v.attempt_number, "created_at": iso(v.created_at)}


def ser_generated(gi: GeneratedImage, latest_validation: ValidationResult | None = None,
                  with_attempts: bool = False) -> dict[str, Any]:
    png_exists = bool(gi.png_path) and Path(gi.png_path).exists()
    webp_exists = bool(gi.webp_path) and Path(gi.webp_path).exists()
    d = {
        "id": gi.id, "image_type": gi.image_type, "label": ImageType.LABEL[gi.image_type], "status": gi.status,
        "png_path": gi.png_path, "webp_path": gi.webp_path, "png_exists": png_exists, "webp_exists": webp_exists,
        "png_name": Path(gi.png_path).name if gi.png_path else None,
        "webp_name": Path(gi.webp_path).name if gi.webp_path else None,
        "png_size": gi.png_size, "webp_size": gi.webp_size, "width": gi.width, "height": gi.height,
        "generation_attempts": gi.generation_attempts, "prompt_version": gi.prompt_version,
        "provider_model": gi.provider_model,
        "model_reference": [Path(p).name for p in (gi.model_reference or "").splitlines() if p],
        "validation_result": gi.validation_result, "validation_confidence": gi.validation_confidence,
        "review_reason": gi.review_reason, "approved": gi.approved, "error": gi.error_message,
        "generated_at": iso(gi.generated_at), "updated_at": iso(gi.updated_at),
        "preview_url": generated_url(gi, "png", 900) if png_exists else None,
        "thumb_url": generated_url(gi, "png", 360) if png_exists else None,
        "png_url": generated_url(gi, "png") if png_exists else None,
        "webp_url": generated_url(gi, "webp") if webp_exists else None,
        "validation": ser_validation(latest_validation),
    }
    if with_attempts:
        d["attempts"] = [ser_attempt(a) for a in gi.attempts]
    return d


def ser_analysis(a: ProductAnalysis | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"source": a.source, "model": a.model, "category": a.category, "product_type": a.product_type,
            "dominant_colour": a.dominant_colour, "secondary_colours": a.secondary_colours or [],
            "metal_appearance": a.metal_appearance, "stones": a.stones, "pearls": a.pearls,
            "design_features": a.design_features or [], "integrity_notes": a.integrity_notes or [],
            "name_parts": a.name_parts or {}, "confidence": a.confidence,
            "notes": [], "created_at": iso(a.created_at)}


def ser_product_summary(p: Product) -> dict[str, Any]:
    stages = stages_for(p)
    gen = {g.image_type: g for g in p.generated}
    active_sources = [s for s in p.sources if s.status != "DUPLICATE"]
    thumb = next((g for g in (gen.get(ImageType.WHITE),) if g is not None and g.png_path and Path(g.png_path).exists()), None)
    return {
        "id": p.id, "product_id": p.product_id, "product_number": p.product_number,
        "product_name": p.product_name, "folder_name": p.folder_name, "category": p.category,
        "status": p.status, "state_label": state_label(p), "on_hold": p.on_hold,
        "grouping_confidence": p.grouping_confidence, "source_count": len(active_sources),
        "progress": progress_for(stages), "stages": stages,
        "images": {t: ({"status": g.status, "thumb_url": generated_url(g, "png", 200) if g.png_path and Path(g.png_path).exists() else None,
                        "validation_confidence": g.validation_confidence} if (g := gen.get(t)) else None)
                   for t in ImageType.ORDER},
        "integrity": _integrity_summary(p),
        "thumb_url": generated_url(thumb, "png", 200) if thumb else (source_url(active_sources[0], 200) if active_sources else None),
        "source_thumb_url": source_url(active_sources[0], 200) if active_sources else None,
        "last_error": p.last_error,
        "created_at": iso(p.created_at), "updated_at": iso(p.updated_at), "completed_at": iso(p.completed_at),
    }


def _integrity_summary(p: Product) -> dict[str, Any]:
    vals = [g.validation_confidence for g in p.generated if g.validation_confidence is not None]
    results = [g.validation_result for g in p.generated if g.validation_result]
    if not results:
        return {"state": "pending", "confidence": None}
    if "FAILED" in results:
        state = "failed"
    elif "REVIEW_REQUIRED" in results and not all(g.approved or g.validation_result != "REVIEW_REQUIRED" for g in p.generated):
        state = "review"
    elif len(results) == len(p.generated) and all(g.status == "COMPLETED" for g in p.generated):
        state = "passed"
    else:
        state = "partial"
    return {"state": state, "confidence": round(min(vals), 3) if vals else None}


def ser_job(j: Job) -> dict[str, Any]:
    return {"id": j.id, "product_id": j.product_id, "kind": j.kind, "status": j.status, "stage": j.stage,
            "started_at": iso(j.started_at), "completed_at": iso(j.completed_at), "error": j.error_message,
            "retry_count": j.retry_count, "created_at": iso(j.created_at)}


def ser_audit(e: AuditEvent) -> dict[str, Any]:
    return {"id": e.id, "timestamp": iso(e.timestamp), "product_id": e.product_id, "product_code": e.product_code,
            "source_image_id": e.source_image_id, "stage": e.stage, "event_type": e.event_type, "level": e.level,
            "message": e.message, "details": e.details}
