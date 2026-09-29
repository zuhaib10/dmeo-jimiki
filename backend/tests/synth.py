"""Synthetic jewellery photographs for tests.

A *design* is a physical product; a *variant* is another photograph of the same
product (different angle, scale, position, exposure, background tint).
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


@dataclass(frozen=True)
class Design:
    metal: tuple[int, int, int]
    stone: tuple[int, int, int]
    accent: tuple[int, int, int]
    petals: int
    drops: int
    shape: str  # flower | hoop | drop


# filename -> design name, so the fake AI vision provider in tests knows the ground truth
TRUTH: dict[str, str] = {}

DESIGNS = {
    "red_jhumka": Design((205, 165, 85), (190, 25, 40), (240, 235, 225), 8, 5, "flower"),
    "green_hoop": Design((190, 190, 196), (30, 130, 70), (30, 130, 70), 12, 0, "hoop"),
    "blue_drop": Design((205, 165, 85), (40, 80, 200), (245, 245, 245), 5, 3, "drop"),
    "pink_flower": Design((212, 150, 130), (230, 120, 170), (250, 250, 250), 6, 0, "flower"),
}


def _earring(draw: ImageDraw.ImageDraw, cx: float, cy: float, d: Design, s: float) -> None:
    if d.shape == "hoop":
        draw.ellipse([cx - 90 * s, cy - 90 * s, cx + 90 * s, cy + 90 * s], outline=d.metal, width=int(16 * s))
        for i in range(d.petals):
            a = 2 * math.pi * i / d.petals
            x, y = cx + 90 * s * math.cos(a), cy + 90 * s * math.sin(a)
            draw.ellipse([x - 11 * s, y - 11 * s, x + 11 * s, y + 11 * s], fill=d.stone, outline=(20, 60, 30))
        return
    r = 70 * s if d.shape == "flower" else 50 * s
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=d.metal, outline=(120, 90, 40), width=3)
    for i in range(d.petals):
        a = 2 * math.pi * i / d.petals
        x, y = cx + r * 0.95 * math.cos(a), cy + r * 0.95 * math.sin(a)
        pr = 20 * s
        draw.ellipse([x - pr, y - pr, x + pr, y + pr], fill=d.stone, outline=(60, 10, 20), width=2)
    draw.ellipse([cx - 18 * s, cy - 18 * s, cx + 18 * s, cy + 18 * s], fill=d.accent, outline=d.metal, width=3)
    top = cy - r - 40 * s
    draw.line([cx, top, cx, cy - r], fill=d.metal, width=int(6 * s))
    for i in range(d.drops):
        x = cx + (i - (d.drops - 1) / 2) * 26 * s
        y0 = cy + r
        y1 = y0 + (60 + 20 * (i % 2)) * s
        draw.line([x, y0, x, y1], fill=d.metal, width=int(4 * s))
        draw.ellipse([x - 11 * s, y1 - 11 * s, x + 11 * s, y1 + 11 * s], fill=d.accent, outline=(150, 140, 120))
    if d.shape == "drop":
        draw.polygon([(cx - 40 * s, cy + r), (cx + 40 * s, cy + r), (cx, cy + r + 150 * s)], fill=d.stone,
                     outline=d.metal)


def render(design: Design, variant: int = 0, size: tuple[int, int] = (1200, 1500), card: bool = False) -> Image.Image:
    rng = random.Random(variant * 7919 + zlib.crc32(repr(design).encode()) % 1000)
    w, h = size
    bg = (246 - rng.randint(0, 8), 242 - rng.randint(0, 8), 236 - rng.randint(0, 8))
    img = Image.new("RGB", (w, h), (205, 200, 192) if card else bg)
    draw = ImageDraw.Draw(img)
    if card:  # JIMIKI-style retail display card: identical in every photograph
        draw.rounded_rectangle([w * 0.12, h * 0.06, w * 0.88, h * 0.94], 30, fill=(250, 250, 248))
        draw.ellipse([w * 0.47, h * 0.09, w * 0.53, h * 0.09 + w * 0.06], fill=(205, 200, 192))
        for i, ch in enumerate("JIMIKI"):
            draw.text((w * 0.40 + i * 40, h * 0.15), ch, fill=(40, 40, 40), font_size=64)
        draw.text((w * 0.38, h * 0.88), "Shop at jimiki.in", fill=(60, 60, 60), font_size=34)
    s = 1.6 * (1 + (rng.random() - 0.5) * 0.25 if variant else 1)
    dx = (rng.random() - 0.5) * 120 if variant else 0
    dy = (rng.random() - 0.5) * 120 if variant else 0
    _earring(draw, w * 0.33 + dx, h * 0.42 + dy, design, s)
    _earring(draw, w * 0.67 + dx, h * 0.42 + dy, design, s)
    if variant and not card:
        img = img.rotate((rng.random() - 0.5) * 24, resample=Image.BICUBIC, fillcolor=bg)
        img = ImageEnhance.Brightness(img).enhance(1 + (rng.random() - 0.5) * 0.12)
    arr = np.asarray(img).astype(np.int16)
    noise = np.random.default_rng(variant + 1).normal(0, 2.0, arr.shape)
    img = Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def write(design_name: str, folder: Path, filename: str, variant: int = 0, card: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    render(DESIGNS[design_name], variant, card=card).save(p, "JPEG", quality=92)
    TRUTH[filename] = design_name
    return p
