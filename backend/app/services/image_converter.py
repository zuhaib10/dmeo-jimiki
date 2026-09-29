"""Output formatting: exact target dimensions, PNG masters and WebP ecommerce copies.

Writes are atomic (temp file + ``os.replace``) so a crash never leaves a
half-written image that could be mistaken for a finished asset.
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from ..models import ImageType


def _edge_colour(img: Image.Image) -> tuple[int, int, int]:
    arr = np.asarray(img.convert("RGB"))
    b = max(2, min(arr.shape[:2]) // 50)
    edge = np.concatenate([arr[:b].reshape(-1, 3), arr[-b:].reshape(-1, 3),
                           arr[:, :b].reshape(-1, 3), arr[:, -b:].reshape(-1, 3)])
    med = np.median(edge, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def fit_to_target(img: Image.Image, width: int, height: int, image_type: str) -> Image.Image:
    img = img.convert("RGB")
    if img.size == (width, height):
        return img
    iw, ih = img.size
    if image_type == ImageType.WHITE:
        # Contain + pad with the (white) background: never crops the product.
        scale = min(width / iw, height / ih)
        resized = img.resize((max(1, round(iw * scale)), max(1, round(ih * scale))), Image.LANCZOS)
        fill = _edge_colour(resized)
        if min(fill) >= 235:
            fill = (255, 255, 255)
        canvas = Image.new("RGB", (width, height), fill)
        canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
        return canvas
    # Cover + centre crop for editorial/close-up photographs.
    scale = max(width / iw, height / ih)
    resized = img.resize((max(width, round(iw * scale)), max(height, round(ih * scale))), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_png(img: Image.Image, path: Path) -> int:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    _atomic_write(path, buf.getvalue())
    return path.stat().st_size


def write_webp(img: Image.Image, path: Path, quality: int) -> int:
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=max(1, min(100, quality)), method=6)
    _atomic_write(path, buf.getvalue())
    return path.stat().st_size


def write_outputs(raw_bytes: bytes, image_type: str, width: int, height: int, png_path: Path, webp_path: Path,
                  webp_quality: int) -> dict[str, int]:
    with Image.open(io.BytesIO(raw_bytes)) as im:
        im.load()
        final = fit_to_target(im, width, height, image_type)
    png_size = write_png(final, png_path)
    webp_size = write_webp(final, webp_path, webp_quality)
    return {"width": final.width, "height": final.height, "png_size": png_size, "webp_size": webp_size}


def rebuild_webp(png_path: Path, webp_path: Path, webp_quality: int) -> int:
    with Image.open(png_path) as im:
        return write_webp(im.convert("RGB"), webp_path, webp_quality)
