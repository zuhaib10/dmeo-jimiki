from __future__ import annotations

import io
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

_TMP = tempfile.mkdtemp(prefix="jimiki-tests-")
os.environ["JIMIKI_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["JIMIKI_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["OPENAI_API_KEY"] = "test-key-not-real"   # enables live mode; real API is never called (fakes injected)
os.environ["IMAGE_PROVIDER"] = "openai"               # ignore the provider chosen in backend/.env
os.environ["JIMIKI_DISABLE_WORKERS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import ai_provider  # noqa: E402
from app.services.ai_provider import (GenerationResult, ImageGroupAI, IntegrityAI, MatchAI,  # noqa: E402
                                      PartitionAI, ProductAnalysisAI, ProviderError)
from synth import TRUTH  # noqa: E402
from app.services.features import segment_foreground  # noqa: E402
from app.services.image_ingestion import analyze_source, list_raw_candidates, register_file  # noqa: E402
from app.services.job_manager import JobManager  # noqa: E402


def _kind(prompt: str) -> str:
    if "Pure white seamless background" in prompt:
        return "white_background"
    if "close-up detail photograph" in prompt:
        return "closeup"
    if "model wearing" in prompt:
        return "model"
    return "closeup"


@dataclass
class FakeImageProvider:
    """Test double: re-composes the *actual source product* onto a new background.

    ``fail`` maps image type → "permanent" or an int number of transient failures.
    ``recolour`` lists image types whose product colours get hue-shifted (integrity failure).
    """
    name: str = "fake"
    model: str = "fake-image-model"
    calls: list[str] = field(default_factory=list)
    fail: dict[str, object] = field(default_factory=dict)
    recolour: set[str] = field(default_factory=set)

    def generation_size(self, width: int, height: int) -> str:
        return f"{-(-width // 16) * 16}x{-(-height // 16) * 16}"

    def generate(self, prompt: str, images: list[tuple[str, bytes, str]], size: str) -> GenerationResult:
        kind = _kind(prompt)
        self.calls.append(kind)
        f = self.fail.get(kind)
        if f == "permanent":
            raise ProviderError("Simulated content-policy rejection", transient=False)
        if isinstance(f, int) and f > 0:
            self.fail[kind] = f - 1
            raise ProviderError("Simulated 503 from provider", transient=True, metadata={"status_code": 503})
        w, h = (int(x) for x in size.split("x"))
        src = Image.open(io.BytesIO(images[0][1])).convert("RGB")
        rgb = np.asarray(src)
        mask, _, _ = segment_foreground(rgb)
        ys, xs = np.where(mask)
        crop = src.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
        cmask = Image.fromarray((mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1] * 255).astype(np.uint8))
        if kind in self.recolour:
            hsv = np.asarray(crop.convert("HSV")).copy()
            hsv[..., 0] = (hsv[..., 0].astype(int) + 110) % 256
            crop = Image.fromarray(hsv, "HSV").convert("RGB")
        bg = {"white_background": (255, 255, 255), "model": (58, 48, 46), "closeup": (120, 110, 100)}[kind]
        canvas = Image.new("RGB", (w, h), bg)
        scale = {"white_background": 0.7, "model": 0.55, "closeup": 1.4}[kind] * min(w / crop.width, h / crop.height)
        cw, ch = int(crop.width * scale), int(crop.height * scale)
        crop, cmask = crop.resize((cw, ch)), cmask.resize((cw, ch))
        canvas.paste(crop, ((w - cw) // 2, (h - ch) // 2), cmask)
        buf = io.BytesIO()
        canvas.save(buf, "PNG")
        return GenerationResult(buf.getvalue(), {"provider": "fake", "size": size})


class FakeVision:
    """Test double for AI vision: knows which synthetic design each photograph shows."""
    name = "fake-vision"

    def _design(self, p: Path) -> str:
        return TRUTH.get(p.name, p.name)

    def partition(self, paths: list[Path]) -> PartitionAI:
        groups: dict[str, list[int]] = {}
        for i, p in enumerate(paths, 1):
            groups.setdefault(self._design(p), []).append(i)
        return PartitionAI(groups=[ImageGroupAI(image_numbers=v, confidence=0.93, reason=f"same {k}")
                                   for k, v in groups.items()])

    def same_product(self, a: list[Path], b: list[Path]) -> MatchAI:
        same = {self._design(p) for p in a} == {self._design(p) for p in b}
        return MatchAI(same_product=same, confidence=0.92 if same else 0.95, reason="fake")

    def analyze_product(self, paths: list[Path]):
        d = self._design(paths[0]).split("_")
        a = ProductAnalysisAI(category="earrings", product_type=f"{d[-1]} earrings", dominant_colour=d[0],
                              secondary_colours=[], metal_appearance="gold-tone", stones=f"{d[0]} stones",
                              pearls=False, design_features=["floral body"], component_inventory=["2 pieces"],
                              integrity_notes=["Preserve stone arrangement"], name_colour=d[0], name_feature="stone",
                              name_type=d[-1], uncertainties=[], confidence=0.9)
        return a, {"model": "fake-vision"}

    def integrity(self, sources, generated, image_type, notes) -> IntegrityAI:
        return IntegrityAI(product_visible=True, same_product=True, confidence=0.9, deviations=[], summary="ok")


@dataclass
class Env:
    root: Path
    manager: JobManager
    provider: FakeImageProvider

    def use_vision(self) -> FakeVision:
        v = FakeVision()
        ai_provider.set_providers(vision=v)
        return v

    @property
    def raw(self) -> Path:
        return settings.raw_dir

    def ingest(self) -> list[int]:
        for p in list_raw_candidates():
            sid = register_file(p)
            if sid:
                analyze_source(sid)
        return self.manager.run_grouping()

    def drain(self) -> None:
        m = self.manager
        while not m.gen_q.empty():
            pk = m.gen_q.get()
            m.process_product(pk)
            m._gen_pending.discard(pk)

    def run(self) -> list[int]:
        touched = self.ingest()
        self.drain()
        return touched


@pytest.fixture
def env(tmp_path: Path):
    database.configure(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    database.init_db()
    root = tmp_path / "JIMIKI"
    settings.set_overrides({"JIMIKI_ROOT": str(root), "RETRY_BASE_DELAY": 0.0, "WORKFLOW_MODE": "live",
                            "MAX_RETRIES": 2, "FILE_STABLE_SECONDS": 0.3, "BATCH_SETTLE_SECONDS": 0.3})
    settings.ensure_directories()
    provider = FakeImageProvider()
    ai_provider.set_providers(vision=None, image=provider)
    yield Env(root, JobManager(), provider)
    ai_provider.clear_overrides()
