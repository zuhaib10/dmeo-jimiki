"""Application configuration.

Precedence (highest first):
1. Values saved from the Settings page (``settings`` table)
2. Environment / ``backend/.env``
3. Built-in defaults below

``OPENAI_API_KEY`` and ``REPLICATE_API_TOKEN`` are only ever read from the
environment and are never returned by the API — only their configured /
not-configured status.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = Path(os.environ.get("JIMIKI_DATA_DIR", BACKEND_DIR / "data"))
LOG_DIR = Path(os.environ.get("JIMIKI_LOG_DIR", BACKEND_DIR / "logs"))
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

RAW_DIRNAME = "raw images"
COMPLETED_DIRNAME = "completed images"
REFERENCE_DIRNAME = "reference model images"
OUTPUT_DIRNAME = "Product Images"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: Any
    cast: Callable[[Any], Any]
    label: str
    description: str
    editable: bool = True
    choices: tuple[str, ...] | None = None
    kind: str = "text"  # text | number | bool | select | path


SPECS: dict[str, SettingSpec] = {
    s.key: s
    for s in [
        SettingSpec("JIMIKI_ROOT", r"D:\Qgraphy\JIMIKI\website and ecommerce product images", str,
                    "JIMIKI root folder", "Root containing raw images, reference model images and Product Images.", kind="path"),
        SettingSpec("IMAGE_WIDTH", 1400, int, "Output width (px)", "Final PNG/WebP width.", kind="number"),
        SettingSpec("IMAGE_HEIGHT", 1600, int, "Output height (px)", "Final PNG/WebP height.", kind="number"),
        SettingSpec("MAX_RETRIES", 3, int, "Max retries", "Retries per image after the first attempt, per job run.", kind="number"),
        SettingSpec("SIMILARITY_THRESHOLD", 0.70, float, "Similarity threshold",
                    "Validation confidence below this sends an image to Review Required.", kind="number"),
        SettingSpec("WORKFLOW_MODE", "live", str, "Workflow mode",
                    "live = generate images; dry_run = stop at QUEUED, never call the image API or move files.",
                    choices=("live", "dry_run"), kind="select"),
        SettingSpec("LOG_LEVEL", "INFO", str, "Log level", "Backend log verbosity.",
                    choices=("DEBUG", "INFO", "WARNING", "ERROR"), kind="select"),
        SettingSpec("ENABLE_PRODUCT_SIMILARITY_CHECK", True, _bool, "Product similarity check",
                    "Run AI product-integrity comparison during validation (in addition to deterministic checks).", kind="bool"),
        SettingSpec("GROUPING_CONFIDENCE_THRESHOLD", 0.75, float, "Grouping confidence threshold",
                    "Images are merged into one product only at or above this confidence.", kind="number"),
        SettingSpec("FILE_STABLE_SECONDS", 3.0, float, "File stable seconds",
                    "A new file must keep the same size for this long before it is registered.", kind="number"),
        SettingSpec("BATCH_SETTLE_SECONDS", 8.0, float, "Batch settle seconds",
                    "Wait this long after the last new file before grouping the batch.", kind="number"),
        SettingSpec("ANALYSIS_MODEL", "gpt-5.5", str, "Analysis model", "OpenAI model for vision analysis."),
        SettingSpec("IMAGE_PROVIDER", "openai", str, "Image provider",
                    "openai = gpt-image edits with all product + reference photos; replicate = Replicate model "
                    "(edits the single sharpest product photo).", choices=("openai", "replicate"), kind="select"),
        SettingSpec("IMAGE_MODEL", "gpt-image-2", str, "Image model", "OpenAI model for image generation."),
        SettingSpec("REPLICATE_IMAGE_MODEL", "xai/grok-imagine-image", str, "Replicate image model",
                    "Replicate model used when the image provider is 'replicate'."),
        SettingSpec("IMAGE_QUALITY", "high", str, "Image quality", "Generation quality.",
                    choices=("low", "medium", "high"), kind="select"),
        SettingSpec("WEBP_QUALITY", 90, int, "WebP quality", "Quality of ecommerce WebP exports (1-100).", kind="number"),
        SettingSpec("RETRY_BASE_DELAY", 2.0, float, "Retry base delay (s)", "Exponential backoff base delay.", kind="number"),
        SettingSpec("OPERATOR_NAME", "Studio Admin", str, "Operator name", "Shown in the dashboard header."),
    ]
}


class Settings:
    """Thread-safe settings view. DB overrides are injected by ``database.load_overrides``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._overrides: dict[str, Any] = {}

    def set_overrides(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._overrides = dict(values)

    def update_override(self, key: str, value: Any) -> None:
        with self._lock:
            self._overrides[key] = value

    def get(self, key: str) -> Any:
        spec = SPECS[key]
        with self._lock:
            if key in self._overrides:
                return spec.cast(self._overrides[key])
        env = os.environ.get(key)
        if env not in (None, ""):
            return spec.cast(env)
        return spec.cast(spec.default)

    def source_of(self, key: str) -> str:
        with self._lock:
            if key in self._overrides:
                return "settings"
        return "env" if os.environ.get(key) else "default"

    def __getattr__(self, item: str) -> Any:
        if item in SPECS:
            return self.get(item)
        raise AttributeError(item)

    # ----- derived values -------------------------------------------------
    @property
    def openai_configured(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())

    @property
    def replicate_configured(self) -> bool:
        return bool(os.environ.get("REPLICATE_API_TOKEN", "").strip())

    @property
    def image_provider_configured(self) -> bool:
        if self.get("IMAGE_PROVIDER") == "replicate":
            return self.replicate_configured
        return self.openai_configured

    @property
    def generation_enabled(self) -> bool:
        return self.get("WORKFLOW_MODE") == "live" and self.image_provider_configured

    @property
    def dry_run_reason(self) -> str | None:
        if self.get("WORKFLOW_MODE") != "live":
            return "Workflow mode is set to DRY RUN"
        if not self.image_provider_configured:
            key = "REPLICATE_API_TOKEN" if self.get("IMAGE_PROVIDER") == "replicate" else "OPENAI_API_KEY"
            return f"{key} is not configured"
        return None

    @property
    def root(self) -> Path:
        return Path(self.get("JIMIKI_ROOT"))

    @property
    def raw_dir(self) -> Path:
        return self.root / RAW_DIRNAME

    @property
    def completed_dir(self) -> Path:
        return self.raw_dir / COMPLETED_DIRNAME

    @property
    def reference_dir(self) -> Path:
        return self.root / REFERENCE_DIRNAME

    @property
    def output_dir(self) -> Path:
        return self.root / OUTPUT_DIRNAME

    def ensure_directories(self) -> None:
        for d in (self.raw_dir, self.completed_dir, self.reference_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)
        for d in (DATA_DIR, DATA_DIR / "cache", DATA_DIR / "generation_raw", LOG_DIR):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
