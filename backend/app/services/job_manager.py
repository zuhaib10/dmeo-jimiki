"""Workflow orchestration.

Two worker threads:

* **intake** — registers stable files, extracts features, waits for the batch
  to settle, groups photographs into products, assigns identity, analyses and
  names products, then queues them.
* **generation** — processes queued products *sequentially*: white → model →
  close-up, validation, retries, PNG/WebP output, and finally archives the
  raw photographs.

All state lives in SQLite, so either worker can be restarted at any point
(see ``recovery.py``) without regenerating successful assets.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from ..database import emit, session_scope
from ..models import (GeneratedImage, GenerationAttempt, GenStatus, ImageType, Job, JobStatus, Product,
                      ProductAnalysis, ProductStatus, SourceImage, SourceStatus, ValidationResult,
                      ValidationResultValue, utcnow)
from . import ai_provider
from .ai_provider import ProviderError, backoff_delay
from .audit_logger import audit
from .events import bus
from .file_archiver import archive_product_sources, archive_single_source
from .image_converter import rebuild_webp, write_outputs
from .image_generator import (describe_references, run_generation, safe_metadata, select_model_references)
from .image_grouping import ExistingProduct, ImageRecord, group_batch, match_existing
from .image_ingestion import analyze_source, register_file
from .image_validator import validate_image
from .product_analyzer import analyze_product
from .product_naming import build_name, file_stem, folder_name
from .product_numbering import allocate_product_number, format_product_id

log = logging.getLogger("jimiki.jobs")


class JobCancelled(Exception):
    pass


def record_of(src: SourceImage) -> ImageRecord:
    return ImageRecord(key=src.id, path=Path(src.current_path), file_hash=src.file_hash, features=src.features or {})


def set_status(s: Session, product: Product, status: str) -> None:
    if product.status != status:
        product.status = status
        emit(s, "product", {"id": product.id, "status": status})


class JobManager:
    def __init__(self) -> None:
        self.intake_q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.gen_q: "queue.Queue[int]" = queue.Queue()
        self._gen_pending: set[int] = set()
        self._gen_lock = threading.Lock()
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._cancel: set[int] = set()
        self.intake_stage = "IDLE"
        self.current_product: int | None = None
        self.current_stage: str | None = None
        self.last_intake_activity = 0.0
        self.watcher: Any = None  # set by main (RawFolderWatcher) — used to know if files are still copying
        self._threads = [threading.Thread(target=self._intake_loop, name="intake", daemon=True),
                         threading.Thread(target=self._generation_loop, name="generation", daemon=True)]

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        self._resume.set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def pause(self) -> None:
        self._resume.clear()
        bus.publish("system")

    def resume(self) -> None:
        self._resume.set()
        bus.publish("system")

    def cancel(self, product_pk: int) -> None:
        self._cancel.add(product_pk)

    def on_file_stable(self, path: Path) -> None:
        self.intake_q.put(("register", path))

    def enqueue_features(self, source_id: int) -> None:
        self.intake_q.put(("features", source_id))

    def enqueue_intake_product(self, product_pk: int) -> None:
        self.intake_q.put(("product", product_pk))

    def enqueue_generation(self, product_pk: int) -> None:
        with self._gen_lock:
            if product_pk in self._gen_pending:
                return
            self._gen_pending.add(product_pk)
        self.gen_q.put(product_pk)

    def queued_ids(self) -> list[int]:
        with self._gen_lock:
            return sorted(self._gen_pending)

    # ------------------------------------------------------------------ intake
    def _intake_loop(self) -> None:
        while not self._stop.is_set():
            try:
                kind, arg = self.intake_q.get(timeout=0.5)
            except queue.Empty:
                kind, arg = None, None
            try:
                if kind == "register":
                    self.intake_stage = "REGISTERING"
                    sid = register_file(arg)
                    if sid is not None:
                        self.intake_stage = "ANALYZING"
                        bus.publish("intake", {"stage": self.intake_stage})
                        analyze_source(sid)
                    self.last_intake_activity = time.time()
                elif kind == "features":
                    self.intake_stage = "ANALYZING"
                    analyze_source(arg)
                    self.last_intake_activity = time.time()
                elif kind == "product":
                    self.analyze_and_name(arg)
                elif kind == "group_now":
                    self.run_grouping()
                else:
                    self._maybe_group()
            except Exception as exc:  # noqa: BLE001
                log.exception("intake error: %s", exc)
                with session_scope() as s:
                    audit(s, "intake_error", f"Intake error: {exc}", level="ERROR")
            finally:
                if self.intake_q.empty() and self.intake_stage not in ("IDLE", "SETTLING"):
                    self.intake_stage = "IDLE"
                    bus.publish("intake", {"stage": "IDLE"})

    def ungrouped_count(self) -> int:
        with session_scope() as s:
            return len(s.scalars(select(SourceImage.id).where(SourceImage.status == SourceStatus.ANALYZED,
                                                              SourceImage.product_id.is_(None))).all())

    def settle_remaining(self) -> float:
        busy = self.watcher is not None and self.watcher.stabilizer.busy
        if busy:
            return float(settings.BATCH_SETTLE_SECONDS)
        last = max(self.last_intake_activity,
                   self.watcher.stabilizer.last_activity if self.watcher is not None else 0.0)
        return max(0.0, float(settings.BATCH_SETTLE_SECONDS) - (time.time() - last))

    def _maybe_group(self) -> None:
        if not self.intake_q.empty() or self.ungrouped_count() == 0:
            if self.intake_stage == "SETTLING":
                self.intake_stage = "IDLE"
            return
        if self.settle_remaining() > 0:
            if self.intake_stage != "SETTLING":
                self.intake_stage = "SETTLING"
                bus.publish("intake", {"stage": "SETTLING"})
            return
        self.run_grouping()

    def run_grouping(self) -> list[int]:
        """Group every analysed-but-ungrouped photograph. Returns new/updated product pks."""
        self.intake_stage = "GROUPING"
        bus.publish("intake", {"stage": "GROUPING"})
        with session_scope() as s:
            pending = s.scalars(select(SourceImage).where(SourceImage.status == SourceStatus.ANALYZED,
                                                          SourceImage.product_id.is_(None))
                                .order_by(SourceImage.id)).all()
            records = [record_of(p) for p in pending if Path(p.current_path).exists()]
            existing_rows = s.scalars(select(Product)).all()
            existing = []
            for prod in existing_rows:
                recs = [record_of(x) for x in prod.sources if x.features and Path(x.current_path).exists()]
                if recs:
                    existing.append(ExistingProduct(prod.id, prod.product_id,
                                                    prod.product_name or prod.product_id, recs))
            if not records:
                return []
            audit(s, "grouping_started", f"Grouping {len(records)} photograph(s)", stage="GROUPING",
                  details={"files": [r.path.name for r in records]})

        vision = ai_provider.get_vision()
        notes: list[str] = []
        threshold = float(settings.GROUPING_CONFIDENCE_THRESHOLD)
        decisions = group_batch(records, vision, threshold, notes)
        by_key = {r.key: r for r in records}
        touched: list[int] = []
        new_products: list[int] = []

        for d in decisions:
            group = [by_key[k] for k in d.keys]
            match, m_conf, m_reason = match_existing(group, existing, vision, threshold)
            with session_scope() as s:
                for n in notes:
                    audit(s, "grouping_note", n, stage="GROUPING", level="WARN")
                notes.clear()
                srcs = [s.get(SourceImage, k) for k in d.keys]
                if match is not None:
                    prod = s.get(Product, match.pk)
                    for src in srcs:
                        src.product_id = prod.id
                        src.status = SourceStatus.GROUPED
                        src.grouping_confidence = m_conf
                        src.grouping_reason = m_reason
                    s.flush()
                    audit(s, "group_assigned",
                          f"{len(srcs)} photograph(s) attached to existing product {prod.product_id} "
                          f"(#{prod.product_number}) — {m_reason}", product=prod, stage="GROUPING",
                          details={"files": [x.original_filename for x in srcs], "confidence": m_conf})
                    if prod.status == ProductStatus.COMPLETED:
                        for src in srcs:
                            archive_single_source(s, prod, src)
                        audit(s, "no_regeneration",
                              "Product already completed — existing assets retained, no regeneration triggered",
                              product=prod)
                    emit(s, "product", {"id": prod.id})
                    touched.append(prod.id)
                    continue

                number = allocate_product_number(s)
                prod = Product(product_id=format_product_id(number), product_number=number,
                               status=ProductStatus.ANALYZING, grouping_confidence=d.confidence,
                               grouping_reason=d.reason)
                s.add(prod)
                s.flush()
                for src in srcs:
                    src.product_id = prod.id
                    src.status = SourceStatus.GROUPED
                    src.grouping_confidence = d.confidence
                    src.grouping_reason = d.reason
                s.flush()
                audit(s, "product_created", f"Product created: {prod.product_id}", product=prod, stage="GROUPING")
                audit(s, "product_number_assigned", f"Product number assigned: {number} ({prod.product_id})",
                      product=prod, stage="GROUPING")
                audit(s, "group_assigned",
                      f"Images grouped: {len(srcs)} photograph(s) → {prod.product_id} "
                      f"(confidence {d.confidence:.2f}) — {d.reason}", product=prod, stage="GROUPING",
                      details={"files": [x.original_filename for x in srcs], "method": d.method,
                               "confidence": d.confidence, **d.pair_scores})
                emit(s, "product", {"id": prod.id})
                new_products.append(prod.id)
                touched.append(prod.id)
                # later groups in this batch may belong to this new product too
                existing.append(ExistingProduct(prod.id, prod.product_id, prod.product_id, group))

        for pk in new_products:
            self.analyze_and_name(pk)
        self.intake_stage = "IDLE"
        bus.publish("intake", {"stage": "IDLE"})
        return touched

    def analyze_and_name(self, product_pk: int) -> None:
        with session_scope() as s:
            prod = s.get(Product, product_pk)
            if prod is None or prod.status not in (ProductStatus.ANALYZING, ProductStatus.NAMING,
                                                   ProductStatus.DISCOVERED, ProductStatus.GROUPING):
                return
            set_status(s, prod, ProductStatus.ANALYZING)
            records = [record_of(x) for x in prod.sources]
            existing_analysis = prod.analysis is not None
        self.intake_stage = "ANALYZING_PRODUCT"
        bus.publish("intake", {"stage": self.intake_stage})

        vision = ai_provider.get_vision()
        if not existing_analysis:
            outcome = analyze_product(records, vision)
            with session_scope() as s:
                prod = s.get(Product, product_pk)
                s.add(ProductAnalysis(
                    product_id=prod.id, source=outcome.source, model=outcome.model, category=outcome.category,
                    product_type=outcome.product_type, dominant_colour=outcome.dominant_colour,
                    secondary_colours=outcome.secondary_colours, metal_appearance=outcome.metal_appearance,
                    stones=outcome.stones, pearls=outcome.pearls, design_features=outcome.design_features,
                    integrity_notes=outcome.integrity_notes, name_parts=outcome.name_parts,
                    confidence=outcome.confidence, raw=outcome.raw))
                prod.category = outcome.category
                audit(s, "product_analyzed",
                      f"Product analysis completed ({outcome.source}) — Category: {outcome.category}, "
                      f"Colour: {outcome.dominant_colour}, Metal: {outcome.metal_appearance}",
                      product=prod, stage="ANALYZING",
                      level="INFO" if outcome.source == "ai" else "WARN",
                      details={"notes": outcome.notes, "confidence": outcome.confidence})
                set_status(s, prod, ProductStatus.NAMING)

        with session_scope() as s:
            prod = s.get(Product, product_pk)
            set_status(s, prod, ProductStatus.NAMING)
            if not prod.product_name:
                parts = prod.analysis.name_parts or {}
                name = build_name(parts.get("colour"), parts.get("feature"), parts.get("type"))
                prod.product_name = name
                prod.folder_name = folder_name(prod.product_number, name)
                audit(s, "product_named", f"Product name generated: {prod.product_number} — {name}",
                      product=prod, stage="NAMING", details={"name_parts": parts})
            self._ensure_generated_rows(s, prod)
            set_status(s, prod, ProductStatus.QUEUED)
            if settings.generation_enabled:
                audit(s, "queued", "Queued for image generation", product=prod, stage="QUEUED")
            else:
                audit(s, "dry_run_hold", f"DRY RUN — image generation disabled ({settings.dry_run_reason}). "
                      "Product identity, analysis and name are saved; source files untouched.",
                      product=prod, stage="QUEUED", level="WARN")
        if settings.generation_enabled:
            self.enqueue_generation(product_pk)

    def _ensure_generated_rows(self, s: Session, prod: Product) -> None:
        have = {g.image_type for g in prod.generated}
        for t in ImageType.ORDER:
            if t not in have:
                s.add(GeneratedImage(product_id=prod.id, image_type=t, status=GenStatus.PENDING))
        s.flush()
        if len(prod.generated) < len(ImageType.ORDER):
            s.refresh(prod)
        worn = [g for g in prod.generated if g.image_type in (ImageType.MODEL, ImageType.CLOSEUP)]
        if any(not g.model_reference for g in worn):
            a = prod.analysis
            refs = select_model_references(settings.reference_dir, a.category if a else None,
                                           a.product_type if a else None, prod.product_number)
            if refs:
                for g in worn:
                    g.model_reference = g.model_reference or "\n".join(str(r) for r in refs)
                audit(s, "references_selected", f"Model references selected: {describe_references([str(r) for r in refs])}",
                      product=prod, stage="QUEUED")
            else:
                audit(s, "references_missing", "No reference model images found — model and close-up images will "
                      "use prompt direction only", product=prod, stage="QUEUED", level="WARN")

    # --------------------------------------------------------------- generation
    def _generation_loop(self) -> None:
        while not self._stop.is_set():
            try:
                pk = self.gen_q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._resume.wait()
            if self._stop.is_set():
                break
            try:
                self.process_product(pk)
            except Exception as exc:  # noqa: BLE001
                log.exception("generation error for product %s: %s", pk, exc)
            finally:
                with self._gen_lock:
                    self._gen_pending.discard(pk)
                self._cancel.discard(pk)
                self.current_product = None
                self.current_stage = None
                bus.publish("system")

    def _check_cancel(self, pk: int) -> None:
        if pk in self._cancel or self._stop.is_set():
            raise JobCancelled()

    def _wait_or_cancel(self, pk: int, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self._check_cancel(pk)
            time.sleep(min(0.25, max(0.0, end - time.time())))

    def process_product(self, pk: int) -> None:
        if not settings.generation_enabled:
            return
        with session_scope() as s:
            prod = s.get(Product, pk)
            if prod is None or prod.on_hold or prod.status in (ProductStatus.ANALYZING, ProductStatus.NAMING):
                return
            statuses = {g.image_type: g.status for g in prod.generated}
            actionable = any(v in (GenStatus.PENDING, GenStatus.GENERATED) for v in statuses.values())
            all_done = statuses and all(v == GenStatus.COMPLETED for v in statuses.values())
            if not actionable and not (all_done and prod.status != ProductStatus.COMPLETED):
                return
            job = Job(product_id=pk, kind="PIPELINE", status=JobStatus.RUNNING, started_at=utcnow(),
                      stage=prod.status)
            s.add(job)
            s.flush()
            job_id = job.id
            sources = [record_of(x) for x in prod.sources]
        self.current_product = pk
        bus.publish("system")

        budgets = {t: 1 + max(0, int(settings.MAX_RETRIES)) for t in ImageType.ORDER}
        try:
            while True:
                self._check_cancel(pk)
                with session_scope() as s:
                    prod = s.get(Product, pk)
                    pending = [g.image_type for g in sorted(prod.generated, key=lambda g: ImageType.ORDER.index(g.image_type))
                               if g.status == GenStatus.PENDING]
                    to_validate = [g.image_type for g in prod.generated if g.status == GenStatus.GENERATED]
                if not pending and not to_validate:
                    break
                for t in pending:
                    self._resume.wait()
                    self._check_cancel(pk)
                    self._generate_one(pk, job_id, t, sources, budgets)
                self._validate_all(pk, job_id, sources, budgets)
            self._finalize(pk, job_id)
        except JobCancelled:
            with session_scope() as s:
                prod = s.get(Product, pk)
                for g in prod.generated:
                    if g.status == GenStatus.GENERATING:
                        g.status = GenStatus.PENDING
                prod.on_hold = True
                set_status(s, prod, ProductStatus.QUEUED)
                job = s.get(Job, job_id)
                job.status, job.completed_at = JobStatus.CANCELLED, utcnow()
                audit(s, "job_stopped", "Job stopped by operator — completed assets kept; press Process to resume",
                      product=prod, level="WARN")
        except Exception as exc:  # noqa: BLE001
            with session_scope() as s:
                prod = s.get(Product, pk)
                prod.last_error = str(exc)[:1000]
                job = s.get(Job, job_id)
                job.status, job.completed_at, job.error_message = JobStatus.FAILED, utcnow(), str(exc)[:1000]
                self._recompute_status(s, prod)
                audit(s, "job_failed", f"Job failed: {exc}", product=prod, level="ERROR")
            raise

    def _paths(self, prod: Product, image_type: str) -> tuple[Path, Path]:
        stem = file_stem(prod.product_number, prod.product_name or prod.product_id.lower())
        folder = settings.output_dir / (prod.folder_name or prod.product_id)
        suffix = ImageType.FILE_SUFFIX[image_type]
        return folder / f"{stem}-{suffix}.png", folder / f"{stem}-{suffix}.webp"

    def _generate_one(self, pk: int, job_id: int, image_type: str, sources: list[ImageRecord],
                      budgets: dict[str, int]) -> None:
        provider = ai_provider.get_image_provider()
        if provider is None:
            raise ProviderError("Image provider not configured", transient=False)
        width, height = int(settings.IMAGE_WIDTH), int(settings.IMAGE_HEIGHT)
        while budgets[image_type] > 0:
            budgets[image_type] -= 1
            self._check_cancel(pk)
            with session_scope() as s:
                prod = s.get(Product, pk)
                gi = next(g for g in prod.generated if g.image_type == image_type)
                gi.status = GenStatus.GENERATING
                gi.generation_attempts += 1
                attempt_no = gi.generation_attempts
                att = GenerationAttempt(generated_image_id=gi.id, attempt_number=attempt_no, status="RUNNING")
                s.add(att)
                s.flush()
                att_id = att.id
                set_status(s, prod, ImageType.PRODUCT_STATUS[image_type])
                job = s.get(Job, job_id)
                job.stage = prod.status
                job.retry_count = max(job.retry_count, attempt_no - 1)
                if image_type in (ImageType.MODEL, ImageType.CLOSEUP) and not gi.model_reference:
                    a = prod.analysis
                    late = select_model_references(settings.reference_dir, a.category if a else None,
                                                   a.product_type if a else None, prod.product_number)
                    if late:
                        gi.model_reference = "\n".join(str(r) for r in late)
                        audit(s, "references_selected", "Model references selected: "
                              f"{describe_references(gi.model_reference.splitlines())}", product=prod)
                refs = gi.model_reference.splitlines() if gi.model_reference else []
                analysis = prod.analysis  # stays readable after commit (expire_on_commit=False)
                audit(s, "generation_started",
                      f"Generating {ImageType.LABEL[image_type].lower()} image (attempt {attempt_no})",
                      product=prod, details={"image_type": image_type, "attempt": attempt_no})
                out_png, out_webp = self._paths(prod, image_type)
                product_code = prod.product_id
            self.current_stage = ImageType.PRODUCT_STATUS[image_type]
            bus.publish("system")
            try:
                result, prompt, used_refs = run_generation(provider, image_type, analysis, sources,
                                                           [Path(r) for r in refs], width, height)
                raw_dir = DATA_DIR / "generation_raw" / product_code
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_path = raw_dir / f"{image_type}-attempt{attempt_no}.png"
                raw_path.write_bytes(result.image_bytes)
                written = write_outputs(result.image_bytes, image_type, width, height, out_png, out_webp,
                                        int(settings.WEBP_QUALITY))
            except (ProviderError, OSError, ValueError) as exc:
                err = exc if isinstance(exc, ProviderError) else ProviderError(str(exc), transient=False)
                with session_scope() as s:
                    prod = s.get(Product, pk)
                    gi = next(g for g in prod.generated if g.image_type == image_type)
                    att = s.get(GenerationAttempt, att_id)
                    att.status, att.finished_at, att.error = "ERROR", utcnow(), str(err)
                    att.provider_metadata = safe_metadata({"transient": err.transient, **err.metadata})
                    gi.error_message = str(err)
                    retry = err.transient and budgets[image_type] > 0
                    gi.status = GenStatus.PENDING if retry else GenStatus.FAILED
                    audit(s, "generation_failed",
                          f"{ImageType.LABEL[image_type]} generation failed (attempt {attempt_no}): {err}"
                          + (" — retry scheduled" if retry else ""), product=prod, level="ERROR" if not retry else "WARN")
                    if retry:
                        audit(s, "retry_triggered", f"Retry triggered for {ImageType.LABEL[image_type].lower()} image",
                              product=prod, level="WARN")
                if not (err.transient and budgets[image_type] > 0):
                    return
                self._wait_or_cancel(pk, backoff_delay(attempt_no, float(settings.RETRY_BASE_DELAY)))
                continue

            with session_scope() as s:
                prod = s.get(Product, pk)
                gi = next(g for g in prod.generated if g.image_type == image_type)
                att = s.get(GenerationAttempt, att_id)
                att.status, att.finished_at = "SUCCESS", utcnow()
                att.raw_output_path = str(raw_path)
                att.provider_metadata = safe_metadata({**result.metadata, "prompt_version": prompt.version})
                gi.png_path, gi.webp_path = str(out_png), str(out_webp)
                gi.width, gi.height = written["width"], written["height"]
                gi.png_size, gi.webp_size = written["png_size"], written["webp_size"]
                gi.prompt_version = prompt.version
                gi.provider_model = getattr(provider, "model", None)
                gi.generated_at = utcnow()
                gi.error_message = None
                gi.status = GenStatus.GENERATED
                gi.validation_result = None
                audit(s, "generation_completed", f"{ImageType.LABEL[image_type]} image completed "
                      f"({written['width']}×{written['height']}, PNG + WebP)", product=prod,
                      details={"png": out_png.name, "webp": out_webp.name, "attempt": attempt_no})
            return

    def _validate_all(self, pk: int, job_id: int, sources: list[ImageRecord], budgets: dict[str, int]) -> None:
        with session_scope() as s:
            prod = s.get(Product, pk)
            targets = [g.image_type for g in prod.generated if g.status == GenStatus.GENERATED]
            if not targets:
                return
            set_status(s, prod, ProductStatus.VALIDATING)
            s.get(Job, job_id).stage = ProductStatus.VALIDATING
            notes = list(prod.analysis.integrity_notes or []) if prod.analysis else []
        self.current_stage = ProductStatus.VALIDATING
        bus.publish("system")
        vision = ai_provider.get_vision()
        for t in targets:
            self._check_cancel(pk)
            with session_scope() as s:
                prod = s.get(Product, pk)
                gi = next(g for g in prod.generated if g.image_type == t)
                gi.status = GenStatus.VALIDATING
                audit(s, "validation_started", f"Validating {ImageType.LABEL[t].lower()} image — product integrity",
                      product=prod)
                png, webp, attempt_no = Path(gi.png_path), Path(gi.webp_path), gi.generation_attempts
            outcome = validate_image(png, webp, t, sources, notes,
                                     (int(settings.IMAGE_WIDTH), int(settings.IMAGE_HEIGHT)),
                                     float(settings.SIMILARITY_THRESHOLD), vision,
                                     bool(settings.ENABLE_PRODUCT_SIMILARITY_CHECK))
            with session_scope() as s:
                prod = s.get(Product, pk)
                gi = next(g for g in prod.generated if g.image_type == t)
                s.add(ValidationResult(generated_image_id=gi.id, product_id=pk, attempt_number=attempt_no,
                                       result=outcome.result, confidence=outcome.confidence, flags=outcome.flags,
                                       checks=outcome.checks, ai_assessment=outcome.ai))
                gi.validation_result = outcome.result
                gi.validation_confidence = outcome.confidence
                label = ImageType.LABEL[t]
                if outcome.result == ValidationResultValue.PASSED:
                    gi.status = GenStatus.COMPLETED
                    gi.review_reason = None
                    audit(s, "validation_passed", f"Validation passed: {label} (confidence {outcome.confidence:.2f})",
                          product=prod, details={"flags": outcome.flags})
                elif outcome.result == ValidationResultValue.REVIEW_REQUIRED:
                    gi.status = GenStatus.REVIEW_REQUIRED
                    gi.review_reason = outcome.summary
                    audit(s, "review_required", f"Review required: {label} — {outcome.summary} "
                          f"(confidence {outcome.confidence:.2f})", product=prod, level="WARN",
                          details={"flags": outcome.flags})
                else:
                    retry = budgets[t] > 0
                    gi.status = GenStatus.PENDING if retry else GenStatus.FAILED
                    gi.error_message = f"Validation failed: {outcome.summary}"
                    audit(s, "validation_failed", f"Validation failed: {label} — {outcome.summary}"
                          + (" — regenerating" if retry else " — retries exhausted"), product=prod,
                          level="WARN" if retry else "ERROR", details={"flags": outcome.flags})
                    if retry:
                        audit(s, "retry_triggered", f"Retry triggered for {label.lower()} image", product=prod,
                              level="WARN")

    def _finalize(self, pk: int, job_id: int) -> None:
        with session_scope() as s:
            prod = s.get(Product, pk)
            job = s.get(Job, job_id)
            statuses = [g.status for g in prod.generated]
            if statuses and all(x == GenStatus.COMPLETED for x in statuses):
                for g in prod.generated:
                    png, webp = Path(g.png_path or ""), Path(g.webp_path or "")
                    if png.is_file() and not webp.is_file():
                        g.webp_size = rebuild_webp(png, webp, int(settings.WEBP_QUALITY))
                        audit(s, "webp_rebuilt", f"WebP rebuilt from PNG master: {webp.name}", product=prod, level="WARN")
                    if not png.is_file():
                        g.status = GenStatus.FAILED
                        g.error_message = "PNG master missing on disk"
                        audit(s, "output_missing", f"{png.name} missing on disk — retry required", product=prod,
                              level="ERROR")
                if all(g.status == GenStatus.COMPLETED for g in prod.generated):
                    set_status(s, prod, ProductStatus.VALIDATING)
                    audit(s, "product_validated", "All three images present and validated", product=prod)
                    archive_product_sources(s, prod)
                    set_status(s, prod, ProductStatus.COMPLETED)
                    prod.completed_at = utcnow()
                    prod.last_error = None
                    audit(s, "product_completed", f"Product completed: {prod.product_number} — {prod.product_name}",
                          product=prod)
                    job.status = JobStatus.COMPLETED
                    job.completed_at = utcnow()
                    job.stage = ProductStatus.COMPLETED
                    return
            self._recompute_status(s, prod)
            job.status = JobStatus.COMPLETED if prod.status != ProductStatus.FAILED else JobStatus.FAILED
            job.completed_at = utcnow()
            job.stage = prod.status

    def _recompute_status(self, s: Session, prod: Product) -> None:
        st = [g.status for g in prod.generated]
        if not st:
            return
        if all(x == GenStatus.COMPLETED for x in st):
            if prod.status != ProductStatus.COMPLETED:
                set_status(s, prod, ProductStatus.VALIDATING)  # awaiting finalize
            return
        if any(x == GenStatus.REVIEW_REQUIRED for x in st):
            if prod.status != ProductStatus.REVIEW_REQUIRED:
                set_status(s, prod, ProductStatus.REVIEW_REQUIRED)
                audit(s, "product_review", "Product requires review before completion — source files kept in raw images",
                      product=prod, level="WARN")
            return
        if any(x in (GenStatus.FAILED, GenStatus.REJECTED) for x in st):
            new = ProductStatus.PARTIAL if any(x == GenStatus.COMPLETED for x in st) else ProductStatus.FAILED
            if prod.status != new:
                set_status(s, prod, new)
                audit(s, "product_" + new.lower(), f"Product {new.lower()} — retry available; source files kept safe",
                      product=prod, level="ERROR" if new == ProductStatus.FAILED else "WARN")
            return
        set_status(s, prod, ProductStatus.QUEUED)

    # ---------------------------------------------------------- operator actions
    def recompute(self, s: Session, prod: Product) -> None:
        self._recompute_status(s, prod)


manager = JobManager()
