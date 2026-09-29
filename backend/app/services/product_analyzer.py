"""Structured product analysis (AI vision when configured, deterministic fallback otherwise)."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .image_grouping import ImageRecord

log = logging.getLogger("jimiki.analysis")

METAL_NAMES = {"gold-tone", "silver-tone", "rose-gold-tone"}
NEUTRAL_NAMES = {"white", "cream", "beige", "grey"}


@dataclass
class AnalysisOutcome:
    source: str
    model: str | None
    category: str
    product_type: str
    dominant_colour: str
    secondary_colours: list[str]
    metal_appearance: str
    stones: str
    pearls: bool | None
    design_features: list[str]
    integrity_notes: list[str]
    name_parts: dict[str, str]
    confidence: float
    raw: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _aggregate_colours(records: list[ImageRecord]) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for r in records:
        for c in r.features.get("dominant", []):
            totals[c["name"]] += c["weight"] / len(records)
    return sorted(totals.items(), key=lambda kv: -kv[1])


def _product_segmented(records: list[ImageRecord]) -> bool:
    """True when the product could be separated from its surroundings in at least one photo."""
    return any(not r.features.get("segmentation_fallback") for r in records)


def deterministic_analysis(records: list[ImageRecord], reason: str) -> AnalysisOutcome:
    colours = _aggregate_colours(records)
    # On a white display card / grey stone, "silver-tone" is usually the card, not the product.
    metal = next((n for n, w in colours if n in METAL_NAMES and w >= 0.12
                  and (n != "silver-tone" or _product_segmented(records))), "")
    feature_colours = [n for n, w in colours if n not in METAL_NAMES and n not in NEUTRAL_NAMES and w >= 0.08]
    dominant = feature_colours[0] if feature_colours else (metal or (colours[0][0] if colours else "unknown"))
    return AnalysisOutcome(
        source="deterministic", model=None,
        category="jewellery (unverified)", product_type="jewellery",
        dominant_colour=dominant,
        secondary_colours=[n for n, _ in colours if n != dominant][:3],
        metal_appearance=metal or "not determined",
        stones="not assessed — AI analysis unavailable",
        pearls=None,
        design_features=[],
        integrity_notes=["Preserve exact shape, colours, stones and all components visible in the source photographs"],
        name_parts={"colour": dominant if dominant not in METAL_NAMES else "",
                    "feature": metal, "type": "jewellery"},
        confidence=0.35,
        raw={"colour_weights": colours},
        notes=[reason],
    )


def analyze_product(records: list[ImageRecord], vision: Any | None) -> AnalysisOutcome:
    ordered = sorted(records, key=lambda r: -float(r.features.get("sharpness", 0)))
    if vision is None:
        return deterministic_analysis(ordered, "AI vision not configured — colour-based analysis only")
    try:
        a, meta = vision.analyze_product([r.path for r in ordered[:6]])
    except Exception as exc:  # noqa: BLE001
        log.warning("AI analysis failed: %s", exc)
        return deterministic_analysis(ordered, f"AI analysis failed ({exc}) — colour-based analysis only")
    return AnalysisOutcome(
        source="ai", model=meta.get("model"),
        category=a.category.strip().lower(), product_type=a.product_type.strip().lower(),
        dominant_colour=a.dominant_colour.strip().lower(),
        secondary_colours=[c.strip().lower() for c in a.secondary_colours],
        metal_appearance=a.metal_appearance.strip().lower(),
        stones=a.stones.strip(), pearls=a.pearls,
        design_features=[f.strip() for f in a.design_features + a.component_inventory if f.strip()],
        integrity_notes=[n.strip() for n in a.integrity_notes if n.strip()],
        name_parts={"colour": a.name_colour, "feature": a.name_feature, "type": a.name_type},
        confidence=max(0.0, min(1.0, float(a.confidence))),
        raw={"analysis": a.model_dump(), "meta": meta},
        notes=[f"Uncertain: {u}" for u in a.uncertainties],
    )


PLACEMENTS = [
    (("hair clip", "hair-clip", "hairclip", "barrette", "hair pin", "hairpin", "claw clip", "snap clip", "alligator clip"),
     "Clipped in the model's hair (e.g. above the ear or on a half-up style), all pieces of the set visible and clearly in focus."),
    (("scrunchie", "hair tie", "hair-tie", "hairtie", "hair band", "ponytail", "rubber band"),
     "Tied around the model's ponytail or bun, clearly visible from a three-quarter back angle."),
    (("headband", "hairband", "hair accessory", "hair"),
     "Worn in the model's hair, clearly visible."),
    (("earring", "jhumka", "stud", "hoop", "chandbali", "ear cuff", "drop"),
     "Worn on the model's ear(s); hair tucked behind the ear; three-quarter head angle so the earring hangs naturally and is fully visible."),
    (("necklace", "choker", "pendant", "chain", "mangalsutra", "haar"),
     "Worn around the model's neck; neckline and collarbones visible so the whole necklace is shown."),
    (("ring",), "Worn on the model's finger; hand posed gracefully near the face or collarbone."),
    (("bangle", "bracelet", "kada", "cuff"), "Worn on the model's wrist; hand and forearm posed naturally."),
    (("tikka", "maang", "matha patti"), "Worn on the forehead at the hair parting, facing camera."),
    (("nose", "nath"), "Worn on the nose, three-quarter face angle."),
    (("anklet", "payal"), "Worn on the ankle."),
]


def placement_for(category: str | None, product_type: str | None) -> str:
    text = f"{category or ''} {product_type or ''}".lower()
    for keys, placement in PLACEMENTS:
        if any(k in text for k in keys):
            return placement
    return "Worn naturally where this type of jewellery is worn, clearly visible."
