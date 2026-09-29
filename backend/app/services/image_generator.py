"""Prompt assembly, reference selection and a single generation call per attempt."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import SUPPORTED_EXTENSIONS
from ..models import ImageType, ProductAnalysis
from .ai_provider import GenerationResult, ImageProvider
from .features import encode_for_upload
from .image_grouping import ImageRecord
from .product_analyzer import placement_for
from .prompts import load_prompt

log = logging.getLogger("jimiki.generator")

PROMPT_NAMES = {ImageType.WHITE: "white_background", ImageType.MODEL: "model_image", ImageType.CLOSEUP: "closeup_image"}
MAX_PRODUCT_INPUTS = 4
MAX_REFERENCE_INPUTS = 2


def select_source_images(records: list[ImageRecord], limit: int = MAX_PRODUCT_INPUTS) -> list[ImageRecord]:
    """Sharpest photographs with a clearly segmented product first."""
    def rank(r: ImageRecord) -> float:
        f = r.features
        return float(f.get("sharpness", 0)) * (0.5 if f.get("segmentation_fallback") else 1.0)
    return sorted(records, key=rank, reverse=True)[:limit]


def list_reference_images(reference_dir: Path) -> list[Path]:
    if not reference_dir.exists():
        return []
    return sorted(p for p in reference_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith("."))


def select_model_references(reference_dir: Path, category: str | None, product_type: str | None,
                            product_number: int, limit: int = MAX_REFERENCE_INPUTS) -> list[Path]:
    """Prefer references whose filename mentions the product category; otherwise rotate
    deterministically by product number so the catalogue gets variety but reruns are stable."""
    refs = list_reference_images(reference_dir)
    if not refs:
        return []
    words = {w for w in f"{category or ''} {product_type or ''}".lower().replace("-", " ").split() if len(w) > 2}
    words |= {w.rstrip("s") for w in words}
    matching = [p for p in refs if any(w in p.stem.lower() for w in words)]
    pool = matching or refs
    start = (product_number - 1) % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(min(limit, len(pool)))]


def _details(a: ProductAnalysis | None) -> str:
    if a is None:
        return "as shown in the product photographs"
    bits = []
    if a.dominant_colour:
        bits.append(f"dominant colour {a.dominant_colour}")
    if a.metal_appearance and a.metal_appearance != "not determined":
        bits.append(f"{a.metal_appearance} finish")
    if a.stones and not a.stones.startswith("not assessed"):
        bits.append(f"stones: {a.stones}")
    if a.pearls:
        bits.append("pearl/pearl-like elements")
    bits += list(a.design_features or [])
    return "; ".join(bits) or "as shown in the product photographs"


@dataclass
class PromptBundle:
    text: str
    version: str


def build_prompt(image_type: str, analysis: ProductAnalysis | None, n_product: int, n_ref: int,
                 width: int, height: int) -> PromptBundle:
    p = load_prompt(PROMPT_NAMES[image_type])
    notes = list(analysis.integrity_notes or []) if analysis else []
    notes.append("Preserve every visible component exactly")
    text = p.render(
        product_type=(analysis.product_type if analysis and analysis.product_type else "fashion jewellery"),
        details=_details(analysis),
        integrity_notes="; ".join(notes),
        placement=placement_for(analysis.category if analysis else None, analysis.product_type if analysis else None),
        aspect=f"{width}×{height} portrait",
        product_image_count=n_product,
        reference_image_count=n_ref,
    )
    return PromptBundle(text, p.version)


def run_generation(provider: ImageProvider, image_type: str, analysis: ProductAnalysis | None,
                   sources: list[ImageRecord], references: list[Path], width: int, height: int
                   ) -> tuple[GenerationResult, PromptBundle, list[str]]:
    limit = getattr(provider, "max_input_images", None)
    chosen = select_source_images(sources, min(MAX_PRODUCT_INPUTS, limit or MAX_PRODUCT_INPUTS))
    refs = references if image_type in (ImageType.MODEL, ImageType.CLOSEUP) else []
    if limit is not None:
        refs = refs[:max(0, limit - len(chosen))]
    prompt = build_prompt(image_type, analysis, len(chosen), len(refs), width, height)
    inputs: list[tuple[str, bytes, str]] = [encode_for_upload(r.path) for r in chosen]
    inputs += [encode_for_upload(p) for p in refs]
    size = provider.generation_size(width, height)
    result = provider.generate(prompt.text, inputs, size)
    result.metadata.setdefault("inputs", [r.path.name for r in chosen] + [p.name for p in refs])
    return result, prompt, [str(p) for p in refs]


def describe_references(paths: list[str]) -> str:
    return ", ".join(Path(p).name for p in paths)


def safe_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Keep provider metadata that is safe to store (no credentials, bounded size)."""
    out = {}
    for k, v in meta.items():
        if "key" in k.lower() or "auth" in k.lower():
            continue
        if isinstance(v, str) and len(v) > 1500:
            v = v[:1500] + "…"
        out[k] = v
    return out
