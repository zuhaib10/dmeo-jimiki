"""Products, generated images and review actions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import emit, get_session
from ..models import (AuditEvent, GeneratedImage, GenStatus, ImageType, Job, Product, ProductStatus, SourceImage,
                      ValidationResult, utcnow)
from ..schemas import (ReviewAction, ser_analysis, ser_audit, ser_generated, ser_job, ser_product_summary, ser_source)
from ..services.audit_logger import audit
from ..services.job_manager import manager, set_status

router = APIRouter(prefix="/api", tags=["products"])

FILTERS = {
    "processing": [ProductStatus.DISCOVERED, ProductStatus.ANALYZING, ProductStatus.GROUPING, ProductStatus.NAMING,
                   ProductStatus.QUEUED, ProductStatus.GENERATING_WHITE, ProductStatus.GENERATING_MODEL,
                   ProductStatus.GENERATING_CLOSEUP, ProductStatus.VALIDATING],
    "completed": [ProductStatus.COMPLETED],
    "review": [ProductStatus.REVIEW_REQUIRED],
    "failed": [ProductStatus.FAILED, ProductStatus.PARTIAL],
}


def get_product(s: Session, ref: str) -> Product:
    q = select(Product).where(Product.product_id == ref.upper()) if not ref.isdigit() else select(Product).where(Product.id == int(ref))
    p = s.scalars(q).first()
    if p is None:
        raise HTTPException(404, "Product not found")
    return p


def latest_validation(s: Session, gi: GeneratedImage) -> ValidationResult | None:
    return s.scalars(select(ValidationResult).where(ValidationResult.generated_image_id == gi.id)
                     .order_by(ValidationResult.id.desc())).first()


@router.get("/products")
def list_products(filter: str = "all", q: str = "", status: str = "", limit: int = Query(500, le=2000),
                  s: Session = Depends(get_session)) -> dict:
    stmt = select(Product)
    if filter in FILTERS:
        stmt = stmt.where(Product.status.in_(FILTERS[filter]))
    if status:
        stmt = stmt.where(Product.status == status)
    if q:
        like = f"%{q.strip().lower()}%"
        conds = [func.lower(Product.product_id).like(like), func.lower(Product.product_name).like(like)]
        if q.strip().isdigit():
            conds.append(Product.product_number == int(q.strip()))
        stmt = stmt.where(or_(*conds))
    rows = s.scalars(stmt.order_by(Product.product_number.desc()).limit(limit)).all()
    counts = dict(s.execute(select(Product.status, func.count()).group_by(Product.status)).all())
    total = sum(counts.values())
    return {"items": [ser_product_summary(p) for p in rows],
            "counts": {"all": total, **{k: sum(counts.get(x, 0) for x in v) for k, v in FILTERS.items()}}}


@router.get("/products/{ref}")
def product_detail(ref: str, s: Session = Depends(get_session)) -> dict:
    p = get_product(s, ref)
    d = ser_product_summary(p)
    d["sources"] = [ser_source(x) for x in p.sources]
    d["analysis"] = ser_analysis(p.analysis)
    if p.analysis is not None and p.analysis.source == "deterministic":
        d["analysis"]["notes"] = ["AI vision not configured — colour-based analysis only"]
    d["generated"] = [ser_generated(g, latest_validation(s, g), with_attempts=True)
                      for g in sorted(p.generated, key=lambda g: ImageType.ORDER.index(g.image_type))]
    d["grouping_reason"] = p.grouping_reason
    d["jobs"] = [ser_job(j) for j in s.scalars(select(Job).where(Job.product_id == p.id).order_by(Job.id.desc()).limit(10))]
    d["events"] = [ser_audit(e) for e in s.scalars(select(AuditEvent).where(AuditEvent.product_id == p.id)
                                                    .order_by(AuditEvent.id.desc()).limit(80))][::-1]
    d["queued"] = p.id in manager.queued_ids()
    d["is_current"] = manager.current_product == p.id
    return d


@router.get("/studio/current")
def studio_current(s: Session = Depends(get_session)) -> dict:
    """Product to show on the Image Studio screen: the one being worked on, else the most recent."""
    if manager.current_product:
        p = s.get(Product, manager.current_product)
        if p:
            return {"id": p.id}
    p = s.scalars(select(Product).where(Product.status.in_(FILTERS["processing"]))
                  .order_by(Product.updated_at.desc())).first() or \
        s.scalars(select(Product).order_by(Product.updated_at.desc())).first()
    return {"id": p.id if p else None}


@router.post("/products/{ref}/process")
def process_product(ref: str, s: Session = Depends(get_session)) -> dict:
    from ..config import settings
    p = get_product(s, ref)
    p.on_hold = False
    if p.status in (ProductStatus.PARTIAL, ProductStatus.FAILED):
        for g in p.generated:
            if g.status in (GenStatus.FAILED, GenStatus.REJECTED):
                g.status = GenStatus.PENDING
                g.error_message = None
        set_status(s, p, ProductStatus.QUEUED)
    audit(s, "process_requested", "Processing requested by operator", product=p)
    s.commit()
    if not settings.generation_enabled:
        return {"queued": False, "reason": settings.dry_run_reason}
    manager.enqueue_generation(p.id)
    return {"queued": True}


@router.post("/products/{ref}/stop")
def stop_product(ref: str, s: Session = Depends(get_session)) -> dict:
    p = get_product(s, ref)
    if manager.current_product == p.id:
        manager.cancel(p.id)
        audit(s, "stop_requested", "Stop requested — the job stops after the current step", product=p, level="WARN")
    else:
        p.on_hold = True
        audit(s, "job_stopped", "Product put on hold by operator", product=p, level="WARN")
        emit(s, "product", {"id": p.id})
    s.commit()
    return {"ok": True}


def _gi(s: Session, gid: int) -> GeneratedImage:
    gi = s.get(GeneratedImage, gid)
    if gi is None:
        raise HTTPException(404, "Generated image not found")
    return gi


@router.post("/generated/{gid}/approve")
def approve(gid: int, body: ReviewAction | None = None, s: Session = Depends(get_session)) -> dict:
    gi = _gi(s, gid)
    if gi.status not in (GenStatus.REVIEW_REQUIRED, GenStatus.GENERATED):
        raise HTTPException(409, f"Cannot approve an image in state {gi.status}")
    gi.status = GenStatus.COMPLETED
    gi.approved, gi.approved_at = True, utcnow()
    p = gi.product
    audit(s, "review_approved", f"{ImageType.LABEL[gi.image_type]} approved by operator"
          + (f": {body.note}" if body and body.note else ""), product=p)
    manager.recompute(s, p)
    emit(s, "product", {"id": p.id})
    s.commit()
    if all(g.status == GenStatus.COMPLETED for g in p.generated):
        manager.enqueue_generation(p.id)  # finalize: archive raw files → COMPLETED
    return {"ok": True}


@router.post("/generated/{gid}/reject")
def reject(gid: int, body: ReviewAction | None = None, s: Session = Depends(get_session)) -> dict:
    gi = _gi(s, gid)
    gi.status = GenStatus.REJECTED
    gi.approved = False
    gi.review_reason = (body.note if body and body.note else gi.review_reason)
    p = gi.product
    audit(s, "review_rejected", f"{ImageType.LABEL[gi.image_type]} rejected by operator — source files preserved",
          product=p, level="WARN")
    manager.recompute(s, p)
    emit(s, "product", {"id": p.id})
    s.commit()
    return {"ok": True}


@router.post("/generated/{gid}/retry")
def retry(gid: int, s: Session = Depends(get_session)) -> dict:
    from ..config import settings
    gi = _gi(s, gid)
    if gi.status in (GenStatus.GENERATING, GenStatus.VALIDATING):
        raise HTTPException(409, "Image is currently being processed")
    gi.status = GenStatus.PENDING
    gi.error_message = None
    gi.approved = False
    p = gi.product
    p.on_hold = False
    if p.status not in ProductStatus.ACTIVE:
        set_status(s, p, ProductStatus.QUEUED)
    audit(s, "retry_triggered", f"Retry requested for {ImageType.LABEL[gi.image_type].lower()} image "
          "(other completed images are kept)", product=p, level="WARN")
    s.commit()
    if settings.generation_enabled:
        manager.enqueue_generation(p.id)
    return {"ok": True, "queued": settings.generation_enabled}


@router.post("/generated/{gid}/review")
def mark_review(gid: int, s: Session = Depends(get_session)) -> dict:
    gi = _gi(s, gid)
    if not gi.png_path:
        raise HTTPException(409, "Nothing to review yet")
    gi.status = GenStatus.REVIEW_REQUIRED
    gi.approved = False
    gi.review_reason = "Marked for review by operator"
    p = gi.product
    audit(s, "marked_for_review", f"{ImageType.LABEL[gi.image_type]} marked for review by operator", product=p,
          level="WARN")
    set_status(s, p, ProductStatus.REVIEW_REQUIRED)
    s.commit()
    return {"ok": True}


@router.get("/review")
def review_queue(s: Session = Depends(get_session)) -> dict:
    rows = s.scalars(select(GeneratedImage).where(GeneratedImage.status == GenStatus.REVIEW_REQUIRED)
                     .order_by(GeneratedImage.updated_at.desc())).all()
    items = []
    for gi in rows:
        p = gi.product
        srcs = [x for x in p.sources if x.status != "DUPLICATE"]
        items.append({"generated": ser_generated(gi, latest_validation(s, gi)),
                      "product": {"id": p.id, "product_id": p.product_id, "product_number": p.product_number,
                                  "product_name": p.product_name, "status": p.status},
                      "sources": [ser_source(x) for x in srcs[:4]]})
    return {"items": items}


@router.get("/completed")
def completed(q: str = "", s: Session = Depends(get_session)) -> dict:
    stmt = select(Product).where(Product.status == ProductStatus.COMPLETED)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Product.product_name).like(like), func.lower(Product.product_id).like(like)))
    rows = s.scalars(stmt.order_by(Product.completed_at.desc())).all()
    from ..config import settings
    out = []
    for p in rows:
        d = ser_product_summary(p)
        d["generated"] = [ser_generated(g) for g in sorted(p.generated, key=lambda g: ImageType.ORDER.index(g.image_type))]
        d["output_folder"] = str(settings.output_dir / (p.folder_name or ""))
        d["archive_folder"] = str(settings.completed_dir / (p.folder_name or ""))
        out.append(d)
    return {"items": out}


@router.get("/sources/unassigned")
def unassigned(s: Session = Depends(get_session)) -> dict:
    rows = s.scalars(select(SourceImage).where(SourceImage.product_id.is_(None)).order_by(SourceImage.id.desc()).limit(100))
    return {"items": [ser_source(x) for x in rows]}
