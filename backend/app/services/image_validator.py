"""Product-integrity validation of generated images.

Deterministic checks catch obvious technical failures (missing/corrupt file,
wrong size, empty image, product not visible, cropping, colour mismatch).
An optional AI comparison against the source photographs looks for
structural deviations. No subjective aesthetic judgements are made.

Outcomes:
* FAILED          — technical failure; the image is regenerated (within retry budget)
* REVIEW_REQUIRED — integrity concern or confidence below threshold; a human decides
* PASSED
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from ..models import ImageType, ValidationResultValue as V
from .features import (colour_presence, colour_similarity, extract_features_from_image, key_colours, open_image,
                       orb_for_image, orb_similarity, pair_similarity, segment_foreground)
from .image_grouping import ImageRecord

log = logging.getLogger("jimiki.validation")

FLAG_LABELS = {
    "missing_component": "Possible missing component",
    "extra_component": "Possible extra component",
    "colour_change": "Dominant colour mismatch",
    "shape_change": "Possible structural change",
    "stone_change": "Possible stone change",
    "material_change": "Possible material change",
    "cropped": "Product partially cropped",
    "extra_jewellery": "Additional jewellery present",
    "other": "Integrity concern",
}


@dataclass
class ValidationOutcome:
    result: str
    confidence: float
    flags: list[dict[str, str]] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    ai: dict[str, Any] | None = None

    @property
    def summary(self) -> str:
        if not self.flags:
            return f"{self.result} (confidence {self.confidence:.2f})"
        return "; ".join(f["label"] for f in self.flags)


def _flag(flags: list[dict[str, str]], code: str, label: str, severity: str) -> None:
    if not any(f["code"] == code for f in flags):
        flags.append({"code": code, "label": label, "severity": severity})


def source_colours(sources: list[ImageRecord]) -> list[dict[str, Any]]:
    """Merge the dominant colours of all source photographs (weights averaged)."""
    merged: dict[str, dict[str, Any]] = {}
    acc: dict[str, float] = defaultdict(float)
    for r in sources:
        for c in r.features.get("dominant", []):
            key = c["name"]
            acc[key] += c["weight"] / max(1, len(sources))
            merged.setdefault(key, c)
    out = [{**merged[k], "weight": round(w, 4)} for k, w in acc.items()]
    return key_colours(sorted(out, key=lambda c: -c["weight"]))


def validate_image(png_path: Path, webp_path: Path, image_type: str, sources: list[ImageRecord],
                   integrity_notes: list[str], target: tuple[int, int], threshold: float,
                   vision: Any | None = None, ai_enabled: bool = True) -> ValidationOutcome:
    flags: list[dict[str, str]] = []
    checks: dict[str, Any] = {}
    tw, th = target

    def hard_fail(code: str, label: str) -> ValidationOutcome:
        _flag(flags, code, label, "fail")
        return ValidationOutcome(V.FAILED, 0.0, flags, checks)

    # ---- technical checks ------------------------------------------------
    checks["file_exists"] = {"passed": png_path.exists() and webp_path.exists()}
    if not checks["file_exists"]["passed"]:
        return hard_fail("missing_file", "Output file missing")
    try:
        with Image.open(png_path) as im:
            im.verify()
        with Image.open(webp_path) as im:
            im.verify()
        checks["file_opens"] = {"passed": True}
    except Exception as exc:  # noqa: BLE001
        checks["file_opens"] = {"passed": False, "error": str(exc)}
        return hard_fail("corrupt", "Malformed output file")

    img = open_image(png_path)
    w, h = img.size
    checks["dimensions"] = {"passed": (w, h) == (tw, th), "value": f"{w}x{h}", "expected": f"{tw}x{th}"}
    if not checks["dimensions"]["passed"]:
        return hard_fail("dimensions", f"Wrong dimensions {w}×{h}")
    ratio_ok = abs(w / h - tw / th) < 0.005
    checks["aspect_ratio"] = {"passed": ratio_ok, "value": round(w / h, 4), "expected": round(tw / th, 4)}

    size = png_path.stat().st_size
    checks["file_size"] = {"passed": 20_000 <= size <= 60_000_000, "value": size}
    if not checks["file_size"]["passed"]:
        return hard_fail("file_size", "Unreasonable file size")

    small = img.copy()
    small.thumbnail((720, 720), Image.LANCZOS)
    rgb = np.asarray(small)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    std = float(gray.std())
    checks["non_empty"] = {"passed": std > 6.0, "value": round(std, 2)}
    if std <= 6.0:
        return hard_fail("empty", "Blank or empty image")

    src_colours = source_colours(sources)

    # ---- product visibility / colour / similarity ------------------------
    if image_type == ImageType.WHITE:
        b = max(2, int(min(rgb.shape[:2]) * 0.02))
        border = np.concatenate([gray[:b].ravel(), gray[-b:].ravel(), gray[:, :b].ravel(), gray[:, -b:].ravel()])
        white_frac = float((border >= 232).mean())
        checks["white_background"] = {"passed": white_frac >= 0.9, "value": round(white_frac, 3)}
        if white_frac < 0.9:
            _flag(flags, "background", "Background not pure white", "review")

        mask, _, fallback = segment_foreground(rgb)
        fg = float(mask.mean())
        checks["product_visible"] = {"passed": 0.015 <= fg and not fallback, "value": round(fg, 4)}
        if fg < 0.015 or fallback:
            return hard_fail("not_visible", "Product not visible")
        edge = np.concatenate([mask[:2].ravel(), mask[-2:].ravel(), mask[:, :2].ravel(), mask[:, -2:].ravel()])
        edge_frac = float(edge.mean())
        checks["cropping"] = {"passed": edge_frac < 0.01, "value": round(edge_frac, 4)}
        if edge_frac >= 0.01 or fg > 0.85:
            _flag(flags, "cropped", "Product partially cropped", "review")

        gen_features = extract_features_from_image(small)
        colour = colour_similarity(src_colours, key_colours(gen_features["dominant"]))
        gen_orb = orb_for_image(small)
        visual = max((pair_similarity(gen_features, s.features, gen_orb, s.orb())["score"] for s in sources),
                     default=0.0)
        checks["dominant_colour"] = {"passed": colour >= 0.5, "value": colour}
        checks["visual_similarity"] = {"value": round(visual, 4)}
        det = 0.55 * colour + 0.45 * min(1.0, visual / 0.6)
    else:
        # jewellery worn on a model is a small part of the frame; a close-up fills it
        presence = colour_presence(rgb, src_colours, target_frac=0.004 if image_type == ImageType.MODEL else 0.02)
        checks["product_visible"] = {"passed": presence >= 0.5, "value": presence,
                                     "method": "source colour presence"}
        gen_orb = orb_for_image(small)
        orb = max((orb_similarity(gen_orb, s.orb()) for s in sources), default=0.0)
        checks["dominant_colour"] = {"passed": presence >= 0.5, "value": presence}
        checks["visual_similarity"] = {"value": round(orb, 4), "method": "ORB keypoints"}
        colour = presence
        det = 0.8 * presence + 0.2 * min(1.0, orb / 0.3)
        if presence < 0.25:
            _flag(flags, "not_visible", "Product colours not found — product may be missing", "review")

    if colour < 0.45:
        _flag(flags, "colour_change", "Dominant colour mismatch", "review")
    det = round(float(det), 4)
    checks["deterministic_confidence"] = {"value": det}

    # ---- AI integrity comparison (optional) ------------------------------
    confidence = det
    ai_payload: dict[str, Any] | None = None
    if vision is not None and ai_enabled:
        try:
            ai = vision.integrity([s.path for s in sources], png_path, image_type, integrity_notes)
            ai_payload = ai.model_dump()
            ai_conf = max(0.0, min(1.0, float(ai.confidence)))
            if not ai.same_product:
                ai_conf = min(ai_conf, 0.4)
            if not ai.product_visible:
                _flag(flags, "not_visible", "Product not clearly visible", "review")
            for d in ai.deviations:
                _flag(flags, d.kind, FLAG_LABELS.get(d.kind, "Integrity concern"),
                      "review" if d.severity == "major" else "info")
            confidence = round(0.65 * ai_conf + 0.35 * det, 4)
            checks["ai_integrity"] = {"passed": ai.same_product and ai_conf >= threshold, "value": ai_conf}
        except Exception as exc:  # noqa: BLE001
            log.warning("AI integrity check failed: %s", exc)
            checks["ai_integrity"] = {"passed": None, "error": str(exc)[:300]}

    if confidence < threshold:
        _flag(flags, "low_confidence", "Low similarity confidence", "review")

    result = V.REVIEW_REQUIRED if any(f["severity"] == "review" for f in flags) else V.PASSED
    return ValidationOutcome(result, round(confidence, 4), flags, checks, ai_payload)
