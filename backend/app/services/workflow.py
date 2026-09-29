"""Derive workflow stages and progress from persisted state (never from timers)."""
from __future__ import annotations

from typing import Any

from ..config import settings
from ..models import GenStatus, ImageType, Product, ProductStatus

STAGES = ["INGEST", "GROUP", "ANALYZE", "NAME", "WHITE", "MODEL", "CLOSEUP", "VALIDATE", "COMPLETE"]
STAGE_LABELS = {
    "INGEST": ("Ingest", "Watch folder"), "GROUP": ("Group", "Same product"), "ANALYZE": ("Analyze", "Identify"),
    "NAME": ("Name", "Number & name"), "WHITE": ("White", "Background"), "MODEL": ("Model", "Editorial"),
    "CLOSEUP": ("Close-up", "Detail"), "VALIDATE": ("Validate", "Integrity"), "COMPLETE": ("Complete", "Save & archive"),
}
IMAGE_STAGE = {"WHITE": ImageType.WHITE, "MODEL": ImageType.MODEL, "CLOSEUP": ImageType.CLOSEUP}

STATE_LABELS = {
    ProductStatus.DISCOVERED: "Files Detected",
    ProductStatus.ANALYZING: "Analyzing Product",
    ProductStatus.GROUPING: "Grouping Images",
    ProductStatus.NAMING: "Product Identified",
    ProductStatus.QUEUED: "Queued",
    ProductStatus.GENERATING_WHITE: "Generating White Background",
    ProductStatus.GENERATING_MODEL: "Generating Model Image",
    ProductStatus.GENERATING_CLOSEUP: "Generating Close-up",
    ProductStatus.VALIDATING: "Validating Product Integrity",
    ProductStatus.COMPLETED: "Completed",
    ProductStatus.PARTIAL: "Partial — Retry Available",
    ProductStatus.FAILED: "Failed — Retry Available",
    ProductStatus.REVIEW_REQUIRED: "Review Required",
}


def _image_state(status: str | None) -> str:
    return {
        GenStatus.COMPLETED: "done", GenStatus.GENERATED: "done", GenStatus.VALIDATING: "done",
        GenStatus.GENERATING: "active", GenStatus.FAILED: "failed", GenStatus.REJECTED: "failed",
        GenStatus.REVIEW_REQUIRED: "review", GenStatus.PENDING: "pending",
    }.get(status or "", "pending")


def stages_for(product: Product) -> list[dict[str, Any]]:
    gen = {g.image_type: g.status for g in product.generated}
    st = product.status
    has_analysis = product.analysis is not None
    dry = not settings.generation_enabled
    out = []
    for key in STAGES:
        if key in ("INGEST", "GROUP"):
            state = "done"
        elif key == "ANALYZE":
            state = "done" if has_analysis else ("active" if st == ProductStatus.ANALYZING else "pending")
        elif key == "NAME":
            state = "done" if product.product_name else ("active" if st == ProductStatus.NAMING else "pending")
        elif key in IMAGE_STAGE:
            state = _image_state(gen.get(IMAGE_STAGE[key]))
            if state == "pending" and dry and st == ProductStatus.QUEUED:
                state = "disabled"
        elif key == "VALIDATE":
            vals = list(gen.values())
            if st == ProductStatus.VALIDATING or GenStatus.VALIDATING in vals:
                state = "active"
            elif vals and all(v == GenStatus.COMPLETED for v in vals):
                state = "done"
            elif GenStatus.REVIEW_REQUIRED in vals:
                state = "review"
            elif any(v in (GenStatus.FAILED, GenStatus.REJECTED) for v in vals) and st in ProductStatus.TERMINAL:
                state = "failed"
            else:
                state = "pending"
        else:  # COMPLETE
            state = "done" if st == ProductStatus.COMPLETED else "pending"
        title, sub = STAGE_LABELS[key]
        out.append({"key": key, "label": title, "sublabel": sub, "state": state})
    return out


def progress_for(stages: list[dict[str, Any]]) -> int:
    done = sum(1 for s in stages if s["state"] == "done")
    return round(100 * done / len(stages))


def state_label(product: Product) -> str:
    if product.status == ProductStatus.QUEUED and not settings.generation_enabled:
        return "Dry Run — Generation Disabled"
    if product.status == ProductStatus.QUEUED and product.on_hold:
        return "Stopped — Press Process to Resume"
    return STATE_LABELS.get(product.status, product.status)
