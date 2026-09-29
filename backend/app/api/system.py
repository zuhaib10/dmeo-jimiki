"""System status, KPIs, queue/inbox, audit log, settings, dry scan, references."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from .. import runtime
from ..config import SPECS, settings
from ..database import emit, get_session
from ..models import AuditEvent, DryRun, Job, Product, ProductStatus, Setting, SourceImage, SourceStatus
from ..schemas import SettingsUpdate, iso, ser_audit, ser_job, ser_product_summary, ser_source
from ..services.audit_logger import audit
from ..services.dry_run import start_dry_scan
from ..services.image_generator import list_reference_images
from ..services.job_manager import manager
from ..services.recovery import requeue_waiting

router = APIRouter(prefix="/api", tags=["system"])
log = logging.getLogger("jimiki.api")


def _storage() -> dict | None:
    for p in (settings.root, settings.root.anchor or "/"):
        try:
            u = shutil.disk_usage(p)
            return {"total": u.total, "used": u.used, "free": u.free, "percent": round(100 * u.used / u.total)}
        except (OSError, ValueError):
            continue
    return None


@router.get("/status")
def status(s: Session = Depends(get_session)) -> dict:
    try:
        s.execute(text("SELECT 1"))
        db = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        db = {"ok": False, "error": str(exc)}
    w = runtime.watcher
    return {
        "api": {"ok": True},
        "database": db,
        "watcher": {"ok": bool(w and w.running), "path": str(settings.raw_dir), "error": w.error if w else "not started"},
        "openai_configured": settings.openai_configured,
        "workflow_mode": settings.WORKFLOW_MODE,
        "generation_enabled": settings.generation_enabled,
        "dry_run_reason": settings.dry_run_reason,
        "paused": manager.paused,
        "current_product": manager.current_product,
        "current_stage": manager.current_stage,
        "intake_stage": manager.intake_stage,
        "queued": manager.queued_ids(),
        "storage": _storage(),
        "operator": settings.OPERATOR_NAME,
        "root": str(settings.root),
        "folders": {"raw": settings.raw_dir.exists(), "completed": settings.completed_dir.exists(),
                    "reference": settings.reference_dir.exists(), "output": settings.output_dir.exists()},
    }


@router.get("/stats")
def stats(s: Session = Depends(get_session)) -> dict:
    counts = dict(s.execute(select(Product.status, func.count()).group_by(Product.status)).all())
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start.astimezone(timezone.utc).replace(tzinfo=None)
    today = s.scalar(select(func.count()).select_from(Product).where(Product.status == ProductStatus.COMPLETED,
                                                                     Product.completed_at >= start_utc)) or 0
    processing = sum(counts.get(x, 0) for x in [ProductStatus.ANALYZING, ProductStatus.NAMING, ProductStatus.QUEUED,
                                               ProductStatus.GENERATING_WHITE, ProductStatus.GENERATING_MODEL,
                                               ProductStatus.GENERATING_CLOSEUP, ProductStatus.VALIDATING])
    sources = s.scalar(select(func.count()).select_from(SourceImage).where(SourceImage.status != SourceStatus.DUPLICATE)) or 0
    return {"products_processed": counts.get(ProductStatus.COMPLETED, 0), "processing": processing,
            "review_required": counts.get(ProductStatus.REVIEW_REQUIRED, 0), "completed_today": today,
            "failed": counts.get(ProductStatus.FAILED, 0) + counts.get(ProductStatus.PARTIAL, 0),
            "total_products": sum(counts.values()), "source_images": sources}


@router.get("/queue")
def queue(s: Session = Depends(get_session)) -> dict:
    active = s.scalars(select(Product).where(Product.status.notin_([ProductStatus.COMPLETED]))
                       .order_by(Product.product_number.desc()).limit(100)).all()
    unassigned = s.scalars(select(SourceImage).where(SourceImage.product_id.is_(None),
                                                     SourceImage.status.in_([SourceStatus.REGISTERED, SourceStatus.ANALYZED,
                                                                             SourceStatus.ERROR]))
                           .order_by(SourceImage.id)).all()
    dups = s.scalars(select(SourceImage).where(SourceImage.status == SourceStatus.DUPLICATE)
                     .order_by(SourceImage.id.desc()).limit(10)).all()
    jobs = s.scalars(select(Job).order_by(Job.id.desc()).limit(25)).all()
    w = runtime.watcher
    return {
        "products": [ser_product_summary(p) for p in active],
        "inbox": {
            "stabilizing": w.stabilizer.snapshot() if w else [],
            "unassigned": [ser_source(x) for x in unassigned],
            "duplicates": [ser_source(x) for x in dups],
            "intake_stage": manager.intake_stage,
            "settle_remaining": round(manager.settle_remaining(), 1) if unassigned else None,
        },
        "worker": {"paused": manager.paused, "current_product": manager.current_product,
                   "current_stage": manager.current_stage, "queued": manager.queued_ids(),
                   "generation_enabled": settings.generation_enabled, "dry_run_reason": settings.dry_run_reason},
        "jobs": [ser_job(j) for j in jobs],
    }


@router.post("/system/pause")
def pause(s: Session = Depends(get_session)) -> dict:
    manager.pause()
    audit(s, "processing_paused", "Processing paused by operator (current step will finish)", level="WARN")
    s.commit()
    return {"paused": True}


@router.post("/system/resume")
def resume(s: Session = Depends(get_session)) -> dict:
    manager.resume()
    audit(s, "processing_resumed", "Processing resumed by operator")
    s.commit()
    return {"paused": False}


@router.post("/system/rescan")
def rescan() -> dict:
    n = runtime.watcher.scan() if runtime.watcher else 0
    return {"new_files": n}


@router.post("/system/group-now")
def group_now() -> dict:
    manager.intake_q.put(("group_now", None))
    return {"ok": True}


@router.get("/audit")
def audit_log(product_id: int | None = None, level: str = "", q: str = "", stage: str = "",
              limit: int = Query(200, le=1000), before: int | None = None, s: Session = Depends(get_session)) -> dict:
    stmt = select(AuditEvent)
    if product_id:
        stmt = stmt.where(AuditEvent.product_id == product_id)
    if level:
        stmt = stmt.where(AuditEvent.level == level.upper())
    if stage:
        stmt = stmt.where(AuditEvent.stage == stage)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(AuditEvent.message).like(like), func.lower(AuditEvent.product_code).like(like),
                              func.lower(AuditEvent.event_type).like(like)))
    if before:
        stmt = stmt.where(AuditEvent.id < before)
    rows = s.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit)).all()
    return {"items": [ser_audit(e) for e in rows]}


# ------------------------------------------------------------------ settings
@router.get("/settings")
def get_settings() -> dict:
    items = []
    for key, spec in SPECS.items():
        items.append({"key": key, "label": spec.label, "description": spec.description, "value": settings.get(key),
                      "default": spec.cast(spec.default), "source": settings.source_of(key), "kind": spec.kind,
                      "choices": list(spec.choices) if spec.choices else None, "editable": spec.editable})
    return {"items": items, "openai_api_key": "Configured" if settings.openai_configured else "Not Configured",
            "replicate_api_token": "Configured" if settings.replicate_configured else "Not Configured",
            "paths": {"root": str(settings.root), "raw": str(settings.raw_dir), "completed": str(settings.completed_dir),
                      "reference": str(settings.reference_dir), "output": str(settings.output_dir)}}


@router.put("/settings")
def update_settings(body: SettingsUpdate, s: Session = Depends(get_session)) -> dict:
    changed = {}
    before_root = settings.JIMIKI_ROOT
    for key, raw in body.values.items():
        spec = SPECS.get(key)
        if spec is None or not spec.editable:
            raise HTTPException(400, f"Unknown or read-only setting {key}")
        try:
            value = spec.cast(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"Invalid value for {key}")
        if spec.choices and str(value) not in spec.choices:
            raise HTTPException(400, f"{key} must be one of {', '.join(spec.choices)}")
        if spec.kind == "number" and float(value) < 0:
            raise HTTPException(400, f"{key} must be positive")
        if value == settings.get(key):
            continue
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value
        settings.update_override(key, value)
        changed[key] = value
    if changed:
        audit(s, "settings_updated", "Settings updated: " + ", ".join(f"{k}={v}" for k, v in changed.items()))
        emit(s, "system")
    s.commit()
    if "LOG_LEVEL" in changed:
        logging.getLogger("jimiki").setLevel(settings.LOG_LEVEL)
    if "JIMIKI_ROOT" in changed and settings.JIMIKI_ROOT != before_root:
        try:
            settings.ensure_directories()
        except OSError as exc:
            raise HTTPException(400, f"Could not create folders under new root: {exc}")
        if runtime.watcher:
            runtime.watcher.restart()
    if "WORKFLOW_MODE" in changed:
        requeue_waiting(manager)
    return get_settings()


# ------------------------------------------------------------------ dry scan
def _ser_dry(r: DryRun | None) -> dict | None:
    if r is None:
        return None
    return {"id": r.id, "status": r.status, "created_at": iso(r.created_at), "report": r.report}


@router.post("/dry-scan")
def dry_scan() -> dict:
    return {"id": start_dry_scan(), "status": "RUNNING"}


@router.get("/dry-scan/latest")
def dry_scan_latest(s: Session = Depends(get_session)) -> dict:
    return {"run": _ser_dry(s.scalars(select(DryRun).order_by(DryRun.id.desc())).first())}


# ------------------------------------------------------------------ references
@router.get("/references")
def references() -> dict:
    from ..services.features import probe_image
    items = []
    for p in list_reference_images(settings.reference_dir):
        try:
            info = probe_image(p)
        except Exception:  # noqa: BLE001
            info = {"width": None, "height": None, "format": p.suffix.lstrip(".").upper()}
        items.append({"name": p.name, "width": info["width"], "height": info["height"], "format": info["format"],
                      "size": p.stat().st_size, "thumb_url": f"/api/media/reference/{p.name}?size=420",
                      "full_url": f"/api/media/reference/{p.name}?size=1600"})
    return {"folder": str(settings.reference_dir), "items": items}
