"""DRY RUN scan.

Scans, hashes, groups, analyses, proposes names and numbers and selects model
references for everything currently in ``raw images`` — without creating
products, consuming product numbers, calling the image-generation API or
moving any file. The report is stored in ``dry_runs``.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..database import emit, session_scope
from ..models import DryRun, Product, SourceImage, SourceStatus, utcnow
from . import ai_provider
from .audit_logger import audit
from .features import extract_features, probe_image, sha256_file
from .image_generator import select_model_references
from .image_grouping import ExistingProduct, ImageRecord, group_batch, match_existing
from .image_ingestion import list_raw_candidates
from .product_analyzer import analyze_product
from .product_naming import build_name, folder_name
from .product_numbering import format_product_id, peek_next_numbers

log = logging.getLogger("jimiki.dryrun")
_lock = threading.Lock()


def start_dry_scan() -> int:
    with session_scope() as s:
        run = DryRun(status="RUNNING", report=None)
        s.add(run)
        s.flush()
        run_id = run.id
        audit(s, "dry_scan_started", "Dry scan started — no images will be generated and no files moved",
              stage="DRY_RUN")
        emit(s, "dry_run", {"id": run_id, "status": "RUNNING"})
    threading.Thread(target=_run_safe, args=(run_id,), name="dry-scan", daemon=True).start()
    return run_id


def _run_safe(run_id: int) -> None:
    try:
        report = run_dry_scan()
        status = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        log.exception("dry scan failed")
        report, status = {"error": str(exc)}, "FAILED"
    with session_scope() as s:
        run = s.get(DryRun, run_id)
        run.status, run.report = status, report
        audit(s, "dry_scan_completed" if status == "COMPLETED" else "dry_scan_failed",
              f"Dry scan {status.lower()}: " + (f"{report.get('files_scanned', 0)} file(s), "
                                                f"{len(report.get('groups', []))} proposed product(s)"
                                                if status == "COMPLETED" else report.get("error", "")),
              stage="DRY_RUN", level="INFO" if status == "COMPLETED" else "ERROR")
        emit(s, "dry_run", {"id": run_id, "status": status})


def run_dry_scan() -> dict[str, Any]:
    with _lock:
        files = list_raw_candidates()
        vision = ai_provider.get_vision()
        report: dict[str, Any] = {"created_at": utcnow().isoformat(), "raw_folder": str(settings.raw_dir),
                                  "ai_used": vision is not None, "files_scanned": len(files),
                                  "already_registered": [], "duplicates": [], "unreadable": [], "groups": [],
                                  "notes": []}
        records: list[ImageRecord] = []
        seen_hash: dict[str, str] = {}
        with session_scope() as s:
            for p in files:
                h = sha256_file(p)
                known = s.scalars(select(SourceImage).where(SourceImage.file_hash == h)).first()
                if known is not None:
                    owner = known.product.product_id if known.product else None
                    report["already_registered"].append({"file": p.name, "product_id": owner,
                                                         "status": known.status})
                    continue
                if h in seen_hash:
                    report["duplicates"].append({"file": p.name, "duplicate_of": seen_hash[h]})
                    continue
                seen_hash[h] = p.name
                try:
                    info = probe_image(p)
                    feats = extract_features(p)
                except Exception as exc:  # noqa: BLE001
                    report["unreadable"].append({"file": p.name, "error": str(exc)})
                    continue
                records.append(ImageRecord(key=p.name, path=p, file_hash=h,
                                           features={**feats, "_info": info}))
            existing = []
            for prod in s.scalars(select(Product)):
                recs = [ImageRecord(x.id, Path(x.current_path), x.file_hash, x.features or {})
                        for x in prod.sources if x.features and Path(x.current_path).exists()
                        and x.status != SourceStatus.DUPLICATE]
                if recs:
                    existing.append(ExistingProduct(prod.id, prod.product_id, prod.product_name or "", recs))

        threshold = float(settings.GROUPING_CONFIDENCE_THRESHOLD)
        decisions = group_batch(records, vision, threshold, report["notes"])
        by_key = {r.key: r for r in records}

        new_groups = []
        for d in decisions:
            group = [by_key[k] for k in d.keys]
            match, conf, reason = match_existing(group, existing, vision, threshold)
            new_groups.append((d, group, match, conf, reason))

        with session_scope() as s:
            numbers = iter(peek_next_numbers(s, sum(1 for g in new_groups if g[2] is None)))

        for d, group, match, conf, reason in new_groups:
            files_info = [{"file": r.path.name, "width": r.features["_info"]["width"],
                           "height": r.features["_info"]["height"], "format": r.features["_info"]["format"],
                           "sha256": r.file_hash[:16], "phash": r.features["phash"],
                           "colours": [c["name"] for c in r.features["dominant"][:3]]} for r in group]
            if match is not None:
                report["groups"].append({"attach_to": match.code, "files": files_info, "confidence": conf,
                                         "reason": reason})
                continue
            outcome = analyze_product(group, vision)
            number = next(numbers)
            name = build_name(outcome.name_parts.get("colour"), outcome.name_parts.get("feature"),
                              outcome.name_parts.get("type"))
            refs = select_model_references(settings.reference_dir, outcome.category, outcome.product_type, number)
            report["groups"].append({
                "proposed_product_id": format_product_id(number), "proposed_number": number,
                "proposed_name": name, "proposed_folder": folder_name(number, name),
                "files": files_info, "confidence": d.confidence, "reason": d.reason, "method": d.method,
                "analysis": {"source": outcome.source, "category": outcome.category,
                             "product_type": outcome.product_type, "dominant_colour": outcome.dominant_colour,
                             "metal_appearance": outcome.metal_appearance, "stones": outcome.stones,
                             "pearls": outcome.pearls, "design_features": outcome.design_features,
                             "integrity_notes": outcome.integrity_notes, "notes": outcome.notes},
                "model_references": [r.name for r in refs],
            })
        report["generation"] = "DISABLED — dry run never calls the image generation API"
        report["files_moved"] = 0
        return report
