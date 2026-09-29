"""AI provider layer.

All vendor-specific code lives here so another provider can be added by
implementing ``VisionProvider`` / ``ImageProvider``. The rest of the
application only talks to these protocols.

When OPENAI_API_KEY is missing, ``get_vision()`` returns ``None``; when the
selected image provider's credential (OPENAI_API_KEY or REPLICATE_API_TOKEN)
is missing, ``get_image_provider()`` returns ``None``. Callers then fall back
to deterministic behaviour. Nothing in
this module fabricates results.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypeVar

from PIL import Image
from pydantic import BaseModel, Field

from ..config import settings
from .features import open_image

log = logging.getLogger("jimiki.ai")

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------
class ProductAnalysisAI(BaseModel):
    category: str = Field(description="Broad category, e.g. earrings, necklace, ring, bangle, maang tikka")
    product_type: str = Field(description="Specific type, e.g. jhumka earrings, hoop earrings, choker necklace")
    dominant_colour: str = Field(description="Most visually important colour of the product (not the background)")
    secondary_colours: list[str]
    metal_appearance: str = Field(description="Visible appearance only, e.g. gold-tone, silver-tone, rose-gold-tone, oxidised")
    stones: str = Field(description="Visible stones described by colour/shape/arrangement, or 'none visible'")
    pearls: bool = Field(description="True only if pearl or pearl-like beads are clearly visible")
    design_features: list[str]
    component_inventory: list[str] = Field(description="Countable components, e.g. '5 hanging pearl drops per earring'")
    integrity_notes: list[str] = Field(description="What an image generator must preserve exactly")
    name_colour: str = Field(description="One colour word for the product name, or empty if uncertain")
    name_feature: str = Field(description="One or two words for the key visible feature, or empty")
    name_type: str = Field(description="Product type words for the name, e.g. jhumka, hoop-earrings")
    uncertainties: list[str]
    confidence: float = Field(description="0-1 confidence in this analysis")


class ImageGroupAI(BaseModel):
    image_numbers: list[int]
    confidence: float = Field(description="0-1 confidence that ALL images in this group show the same physical product")
    reason: str


class PartitionAI(BaseModel):
    groups: list[ImageGroupAI]


class MatchAI(BaseModel):
    same_product: bool
    confidence: float
    reason: str


class DeviationAI(BaseModel):
    kind: Literal["missing_component", "extra_component", "colour_change", "shape_change", "stone_change",
                  "material_change", "cropped", "extra_jewellery", "other"]
    severity: Literal["minor", "major"]
    description: str


class IntegrityAI(BaseModel):
    product_visible: bool
    same_product: bool
    confidence: float = Field(description="0-1 confidence that the jewellery is unchanged from the source photographs")
    deviations: list[DeviationAI]
    summary: str


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------
@dataclass
class GenerationResult:
    image_bytes: bytes
    metadata: dict[str, Any] = field(default_factory=dict)


class VisionProvider(Protocol):
    name: str

    def analyze_product(self, paths: list[Path]) -> tuple[ProductAnalysisAI, dict[str, Any]]: ...
    def partition(self, paths: list[Path]) -> PartitionAI: ...
    def same_product(self, a: list[Path], b: list[Path]) -> MatchAI: ...
    def integrity(self, sources: list[Path], generated: Path, image_type: str, notes: list[str]) -> IntegrityAI: ...


class ImageProvider(Protocol):
    """Providers may also set ``max_input_images`` (int) when they accept fewer inputs than we'd send."""

    name: str
    model: str

    def generation_size(self, width: int, height: int) -> str: ...
    def generate(self, prompt: str, images: list[tuple[str, bytes, str]], size: str) -> GenerationResult: ...


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    def __init__(self, message: str, transient: bool, metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.transient = transient
        self.metadata = metadata or {}


def classify_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    try:
        import openai
        transient_types: tuple[type[BaseException], ...] = (openai.RateLimitError, openai.APIConnectionError,
                                                            openai.APITimeoutError, openai.InternalServerError)
        status = getattr(exc, "status_code", None)
        meta = {"status_code": status, "error_type": type(exc).__name__}
        req = getattr(exc, "request_id", None)
        if req:
            meta["request_id"] = req
        transient = isinstance(exc, transient_types) or (status is not None and status >= 500)
        return ProviderError(_short(str(exc)), transient, meta)
    except ImportError:  # pragma: no cover
        return ProviderError(_short(str(exc)), False, {"error_type": type(exc).__name__})


def _short(msg: str, n: int = 600) -> str:
    return msg if len(msg) <= n else msg[:n] + "…"


def backoff_delay(attempt: int, base: float) -> float:
    """Exponential backoff with jitter; attempt is 1-based."""
    if base <= 0:
        return 0.0
    return min(60.0, base * (2 ** (attempt - 1))) * (0.8 + 0.4 * random.random())


def with_backoff(fn: Callable[[], T], retries: int, base: float, what: str) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            err = classify_error(exc)
            if not err.transient or attempt > retries:
                raise err from exc
            delay = backoff_delay(attempt, base)
            log.warning("%s failed (attempt %s, transient): %s — retrying in %.1fs", what, attempt, err, delay)
            time.sleep(delay)


# ---------------------------------------------------------------------------
# OpenAI implementation
# ---------------------------------------------------------------------------
def _data_url(path: Path, max_side: int) -> str:
    img = open_image(path, max_side)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _client() -> Any:
    from openai import OpenAI
    # Retries are handled by our own backoff so every attempt can be recorded.
    return OpenAI(max_retries=0, timeout=300)


class OpenAIVision:
    name = "openai"

    def __init__(self, model: str) -> None:
        self.model = model
        self.client = _client()

    def _parse(self, instructions: str, content: list[dict[str, Any]], schema: type[T], what: str) -> tuple[T, dict[str, Any]]:
        def call() -> Any:
            return self.client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                text_format=schema,
            )

        resp = with_backoff(call, settings.MAX_RETRIES, settings.RETRY_BASE_DELAY, what)
        parsed = resp.output_parsed
        if parsed is None:
            raise ProviderError(f"{what}: model returned no structured output", transient=False)
        usage = getattr(resp, "usage", None)
        meta = {"model": getattr(resp, "model", self.model), "response_id": getattr(resp, "id", None),
                "usage": usage.model_dump() if hasattr(usage, "model_dump") else None}
        return parsed, meta

    @staticmethod
    def _images(paths: list[Path], label: str, max_side: int = 1024, detail: str = "high") -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for i, p in enumerate(paths, 1):
            parts.append({"type": "input_text", "text": f"{label} {i}:"})
            parts.append({"type": "input_image", "image_url": _data_url(p, max_side), "detail": detail})
        return parts

    def analyze_product(self, paths: list[Path]) -> tuple[ProductAnalysisAI, dict[str, Any]]:
        from .prompts import load_prompt
        prompt = load_prompt("product_analysis")
        content = [{"type": "input_text", "text": prompt.text}] + self._images(paths[:6], "Photograph")
        parsed, meta = self._parse("You are a meticulous jewellery cataloguer. Report only what is visible.",
                                   content, ProductAnalysisAI, "product analysis")
        meta["prompt_version"] = prompt.version
        return parsed, meta

    def partition(self, paths: list[Path]) -> PartitionAI:
        text = (
            f"There are {len(paths)} photographs numbered 1..{len(paths)}. Several photographs may show the SAME "
            "single physical jewellery product from different angles, distances, lighting or backgrounds. "
            "Partition ALL photograph numbers into groups so that each group contains photographs of exactly one "
            "physical product (same design, same colours, same stones, same components). A pair of earrings counts "
            "as one product. Be conservative: if two photographs might be different products (e.g. same style in a "
            "different colour, different stone arrangement, different number of drops), put them in separate "
            "groups. Every number must appear in exactly one group. Give a confidence for each group. "
            "IMPORTANT: most photographs show products mounted on the same white JIMIKI display card on the "
            "same stone prop — ignore the card, logo, printing and props completely and compare only the products."
        )
        content = [{"type": "input_text", "text": text}] + self._images(paths[:16], "Photograph", 640, "high")
        parsed, _ = self._parse("You identify whether photographs show the same physical jewellery item.",
                                content, PartitionAI, "image grouping")
        return parsed

    def same_product(self, a: list[Path], b: list[Path]) -> MatchAI:
        text = ("Set A and Set B are photographs of jewellery. Is Set A the SAME physical product (identical design, "
                "colours, stones and components) as Set B? Different colourways or variants of a design are NOT the "
                "same product. Ignore the JIMIKI display card, logo and props — compare only the products. "
                "Be conservative.")
        content = ([{"type": "input_text", "text": text}] + self._images(a[:3], "Set A photo", 640)
                   + self._images(b[:3], "Set B photo", 640))
        parsed, _ = self._parse("You verify product identity for a jewellery catalogue.", content, MatchAI,
                                "existing product match")
        return parsed

    def integrity(self, sources: list[Path], generated: Path, image_type: str, notes: list[str]) -> IntegrityAI:
        text = (
            "The SOURCE photographs show a real JIMIKI jewellery product. The GENERATED image is an ecommerce "
            f"'{image_type.replace('_', ' ')}' photograph that must show the exact same product. The environment, "
            "model and lighting may differ; the jewellery may not. Compare the jewellery only and list any deviations: "
            "missing or extra components, changed colour, changed shape, changed stones/arrangement, changed material "
            "appearance, cropping of the product, or additional jewellery that is not the product. The JIMIKI display "
            "card and props in the SOURCE photos are not part of the product; flag it (kind 'other') if the GENERATED "
            "image shows a display card, logo or text. Do not judge "
            "aesthetics. Integrity notes to check: " + "; ".join(notes or ["none"])
        )
        content = ([{"type": "input_text", "text": text}] + self._images(sources[:3], "SOURCE", 896)
                   + [{"type": "input_text", "text": "GENERATED:"},
                      {"type": "input_image", "image_url": _data_url(generated, 1024), "detail": "high"}])
        parsed, _ = self._parse("You are a strict product-integrity inspector for jewellery ecommerce imagery.",
                                content, IntegrityAI, "integrity check")
        return parsed


ARBITRARY_SIZE_PREFIXES = ("gpt-image-2",)
INPUT_FIDELITY_PREFIXES = ("gpt-image-1",)


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, model: str, quality: str) -> None:
        self.model = model
        self.quality = quality
        self.client = _client()

    def generation_size(self, width: int, height: int) -> str:
        if self.model.startswith(ARBITRARY_SIZE_PREFIXES):
            # Width/height must be multiples of 16; round up and trim afterwards.
            return f"{-(-width // 16) * 16}x{-(-height // 16) * 16}"
        return "1024x1536" if height >= width else "1536x1024"

    def generate(self, prompt: str, images: list[tuple[str, bytes, str]], size: str) -> GenerationResult:
        kwargs: dict[str, Any] = dict(model=self.model, image=images, prompt=prompt, size=size, n=1,
                                      quality=self.quality, output_format="png")
        if self.model.startswith(INPUT_FIDELITY_PREFIXES):
            kwargs["input_fidelity"] = "high"
        started = time.time()
        try:
            resp = self.client.images.edit(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise classify_error(exc) from exc
        if not resp.data or not resp.data[0].b64_json:
            raise ProviderError("Image API returned no image data", transient=True)
        data = base64.b64decode(resp.data[0].b64_json)
        usage = getattr(resp, "usage", None)
        meta = {
            "provider": self.name, "model": self.model, "size": size, "quality": self.quality,
            "duration_s": round(time.time() - started, 2), "created": getattr(resp, "created", None),
            "usage": usage.model_dump() if hasattr(usage, "model_dump") else None,
            "revised_prompt": getattr(resp.data[0], "revised_prompt", None),
            "request_id": getattr(resp, "_request_id", None),
        }
        with Image.open(io.BytesIO(data)) as im:
            meta["returned_size"] = f"{im.width}x{im.height}"
        return GenerationResult(data, meta)


# ---------------------------------------------------------------------------
# Replicate implementation
# ---------------------------------------------------------------------------
REPLICATE_ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2",
                           "19.5:9", "9:19.5", "20:9", "9:20")


def _classify_replicate_error(exc: Exception) -> ProviderError:
    import httpx
    from replicate.exceptions import ModelError, ReplicateError

    if isinstance(exc, ModelError):
        pred = getattr(exc, "prediction", None)
        meta = {"error_type": "ModelError", "prediction_id": getattr(pred, "id", None)}
        return ProviderError(_short(f"Replicate prediction failed: {exc}"), False, meta)
    if isinstance(exc, ReplicateError):
        status = getattr(exc, "status", None)
        meta = {"status_code": status, "error_type": type(exc).__name__}
        return ProviderError(_short(str(exc)), status is not None and (status == 429 or status >= 500), meta)
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return ProviderError(_short(str(exc) or type(exc).__name__), True, {"error_type": type(exc).__name__})
    return ProviderError(_short(str(exc)), False, {"error_type": type(exc).__name__})


class ReplicateImageProvider:
    """Replicate image-edit models that take one ``image`` + ``prompt`` (e.g. xai/grok-imagine-image)."""

    name = "replicate"
    # The model edits a single input image, so only the sharpest product photo is sent.
    max_input_images = 1

    def __init__(self, model: str) -> None:
        import replicate
        self.model = model
        self.client = replicate.Client(timeout=300)

    def generation_size(self, width: int, height: int) -> str:
        # Only used for text-to-image; the model keeps the input image's framing when editing.
        target = width / height

        def ratio(r: str) -> float:
            a, b = r.split(":")
            return float(a) / float(b)
        return min(REPLICATE_ASPECT_RATIOS, key=lambda r: abs(ratio(r) - target))

    def generate(self, prompt: str, images: list[tuple[str, bytes, str]], size: str) -> GenerationResult:
        model_input: dict[str, Any] = {"prompt": prompt, "aspect_ratio": size}
        if images:
            # Sent inline: Replicate file-upload URLs have no extension, which grok-imagine rejects.
            _filename, data, mime = images[0]
            model_input["image"] = f"data:{mime};base64," + base64.b64encode(data).decode()
        started = time.time()
        try:
            output = self.client.run(self.model, input=model_input)
            if isinstance(output, list):
                output = output[0] if output else None
            if output is None:
                raise ProviderError("Replicate returned no image", transient=True)
            raw = output.read()
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify_replicate_error(exc) from exc
        # Normalise to PNG so raw attempts match the OpenAI provider's output format.
        with Image.open(io.BytesIO(raw)) as im:
            returned_size = f"{im.width}x{im.height}"
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "PNG")
        meta = {
            "provider": self.name, "model": self.model, "aspect_ratio": size,
            "duration_s": round(time.time() - started, 2), "output_url": str(getattr(output, "url", "")) or None,
            "returned_size": returned_size,
        }
        return GenerationResult(buf.getvalue(), meta)


# ---------------------------------------------------------------------------
# Factory (tests can inject fakes)
# ---------------------------------------------------------------------------
_overrides: dict[str, Any] = {}


def set_providers(vision: Any = "unset", image: Any = "unset") -> None:
    if vision != "unset":
        _overrides["vision"] = vision
    if image != "unset":
        _overrides["image"] = image


def clear_overrides() -> None:
    _overrides.clear()


def get_vision() -> VisionProvider | None:
    if "vision" in _overrides:
        return _overrides["vision"]
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    return OpenAIVision(settings.ANALYSIS_MODEL)


def get_image_provider() -> ImageProvider | None:
    if "image" in _overrides:
        return _overrides["image"]
    if settings.IMAGE_PROVIDER == "replicate":
        if not os.environ.get("REPLICATE_API_TOKEN"):
            return None
        return ReplicateImageProvider(settings.REPLICATE_IMAGE_MODEL)
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    return OpenAIImageProvider(settings.IMAGE_MODEL, settings.IMAGE_QUALITY)
