"""Deterministic image features used for grouping, analysis fallback and validation.

Everything here is pure OpenCV / NumPy / Pillow — no network calls.
Source photographs are only ever opened read-only.
"""
from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from ..config import DATA_DIR

log = logging.getLogger("jimiki.features")

try:  # HEIC support (iPhone photos)
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:  # pragma: no cover
    HEIC_SUPPORTED = False

ANALYSIS_SIDE = 768

# Named reference colours used for human-readable colour names.
COLOUR_PALETTE: dict[str, tuple[int, int, int]] = {
    "red": (196, 30, 45), "maroon": (115, 22, 38), "pink": (232, 128, 168), "orange": (230, 118, 35),
    "yellow": (238, 206, 60), "gold-tone": (204, 164, 82), "green": (46, 148, 72), "dark-green": (20, 90, 60),
    "teal": (32, 140, 140), "blue": (42, 92, 200), "navy": (26, 36, 92), "purple": (118, 52, 150),
    "white": (246, 246, 244), "cream": (238, 226, 200), "silver-tone": (188, 188, 194), "grey": (125, 125, 128),
    "black": (22, 22, 24), "brown": (108, 68, 40), "beige": (214, 194, 158), "rose-gold-tone": (212, 150, 130),
}
_PALETTE_LAB = {k: cv2.cvtColor(np.uint8([[v]]), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
                for k, v in COLOUR_PALETTE.items()}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def open_image(path: Path, max_side: int | None = None) -> Image.Image:
    """Open read-only, apply EXIF orientation, return RGB (optionally downscaled)."""
    with Image.open(path) as im:
        if max_side and im.format == "JPEG":
            im.draft("RGB", (max_side, max_side))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        return im.copy()


def probe_image(path: Path) -> dict[str, Any]:
    """Verify the file opens and return format + true (oriented) dimensions."""
    with Image.open(path) as im:
        fmt = (im.format or path.suffix.lstrip(".")).upper()
        im.verify()
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
    mime = Image.MIME.get(fmt) or {"HEIF": "image/heic", "HEIC": "image/heic"}.get(fmt, "application/octet-stream")
    return {"format": "JPG" if fmt == "JPEG" else fmt, "width": w, "height": h, "mime": mime}


def to_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------
def phash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)[:8, :8]
    med = np.median(dct.flatten()[1:])
    bits = (dct > med).flatten()
    return "%016x" % int("".join("1" if b else "0" for b in bits), 2)


def dhash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return "%016x" % int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------------------------------------------------------------------------
# Segmentation & colour
# ---------------------------------------------------------------------------
def segment_foreground(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, bool]:
    """Separate the product from a (roughly uniform) photographic background.

    Returns (mask bool[h,w], background rgb, used_fallback).
    """
    h, w = rgb.shape[:2]
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    b = max(2, int(min(h, w) * 0.04))
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                             lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(lab - bg, axis=2)
    noise = float(np.percentile(np.linalg.norm(border - bg, axis=1), 95))
    d8 = np.clip(dist * 2, 0, 255).astype(np.uint8)
    otsu, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Otsu alone splits between product classes (e.g. silver metal vs coloured stones), so cap its
    # influence and let the background noise level drive the threshold.
    thr = max(min(otsu / 2.0, 25.0), noise * 2.5, 12.0)
    mask = (dist > thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    min_area = h * w * 0.001
    keep = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep[labels == i] = 1
    bg_rgb = cv2.cvtColor(np.uint8([[bg]]), cv2.COLOR_LAB2RGB)[0, 0]
    if keep.mean() < 0.002:
        yy, xx = np.ogrid[:h, :w]
        ell = ((xx - w / 2) / (w * 0.3)) ** 2 + ((yy - h / 2) / (h * 0.3)) ** 2 <= 1
        return ell, bg_rgb, True
    return keep.astype(bool), bg_rgb, False


def colour_name(rgb: np.ndarray | list[int] | tuple[int, ...]) -> str:
    lab = cv2.cvtColor(np.uint8([[list(rgb)]]), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
    return min(_PALETTE_LAB, key=lambda k: float(np.linalg.norm(_PALETTE_LAB[k] - lab)))


def dominant_colours(rgb: np.ndarray, mask: np.ndarray | None = None, k: int = 4) -> list[dict[str, Any]]:
    px = rgb[mask] if mask is not None else rgb.reshape(-1, 3)
    if len(px) < k:
        return []
    if len(px) > 20000:
        idx = np.random.default_rng(7).choice(len(px), 20000, replace=False)
        px = px[idx]
    lab = cv2.cvtColor(px.reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    cv2.setRNGSeed(7)
    _, labels, centers = cv2.kmeans(lab, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    out = []
    for i in np.argsort(-counts):
        c_lab = np.clip(centers[i], 0, 255).astype(np.uint8)
        c_rgb = cv2.cvtColor(np.uint8([[c_lab]]), cv2.COLOR_LAB2RGB)[0, 0]
        out.append({"rgb": [int(x) for x in c_rgb], "lab": [float(x) for x in centers[i]],
                    "weight": round(float(counts[i] / counts.sum()), 4), "name": colour_name(c_rgb)})
    return out


def colour_hist(rgb: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    """Lab histogram: coarse lightness, fine chroma (a/b). Stable for neutrals and exposure changes."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    m = mask.astype(np.uint8) * 255 if mask is not None else None
    hist = cv2.calcHist([lab], [0, 1, 2], m, [3, 10, 10], [0, 256, 64, 192, 64, 192])
    cv2.normalize(hist, hist, 1.0, 0, cv2.NORM_L1)
    return hist.flatten()


# Low-chroma colours that are usually the display card, stone prop or backdrop rather than the product.
NEUTRAL_COLOURS = {"white", "cream", "beige", "grey", "silver-tone"}


def key_colours(colours: list[dict[str, Any]], min_total: float = 0.08) -> list[dict[str, Any]]:
    """The product's characteristic colours: chromatic colours when present, else everything.

    JIMIKI photographs show products on a white branded card over a stone prop, which dominate
    the palette; weighting only chromatic colours keeps checks focused on the product itself.
    """
    chroma = [c for c in colours if c["name"] not in NEUTRAL_COLOURS]
    total = sum(c["weight"] for c in chroma)
    if total < min_total:
        return colours
    return [{**c, "weight": round(c["weight"] / total, 4)} for c in chroma]


def colour_similarity(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> float:
    """Weighted nearest-colour agreement in Lab space (0..1), symmetric."""
    if not a or not b:
        return 0.0

    def one_way(x: list[dict[str, Any]], y: list[dict[str, Any]]) -> float:
        ylab = np.array([c["lab"] for c in y], dtype=np.float32)
        total = 0.0
        for c in x:
            d = float(np.min(np.linalg.norm(ylab - np.array(c["lab"], dtype=np.float32), axis=1)))
            total += c["weight"] * max(0.0, 1.0 - d / 45.0)
        return total / max(1e-6, sum(c["weight"] for c in x))

    return round((one_way(a, b) + one_way(b, a)) / 2, 4)


def colour_presence(rgb: np.ndarray, colours: list[dict[str, Any]], min_weight: float = 0.12,
                    tolerance: float = 22.0, target_frac: float = 0.02) -> float:
    """How well the key colours of a product are present anywhere in ``rgb`` (0..1)."""
    key = [c for c in colours if c["weight"] >= min_weight] or colours[:1]
    if not key:
        return 0.0
    small = cv2.resize(rgb, (256, int(256 * rgb.shape[0] / rgb.shape[1])), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    score = 0.0
    for c in key:
        d = np.linalg.norm(lab - np.array(c["lab"], dtype=np.float32), axis=1)
        frac = float((d < tolerance).mean())
        score += c["weight"] * min(1.0, frac / target_frac)
    return round(score / sum(c["weight"] for c in key), 4)


def sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def bbox_of(mask: np.ndarray) -> list[float]:
    ys, xs = np.where(mask)
    h, w = mask.shape
    if len(xs) == 0:
        return [0.0, 0.0, 1.0, 1.0]
    return [round(xs.min() / w, 4), round(ys.min() / h, 4), round((xs.max() + 1) / w, 4), round((ys.max() + 1) / h, 4)]


def crop_to_mask(arr: np.ndarray, mask: np.ndarray, pad: float = 0.05) -> np.ndarray:
    h, w = mask.shape
    x0, y0, x1, y1 = bbox_of(mask)
    px, py = pad * (x1 - x0), pad * (y1 - y0)
    xa, ya = max(0, int((x0 - px) * w)), max(0, int((y0 - py) * h))
    xb, yb = min(w, int((x1 + px) * w)), min(h, int((y1 + py) * h))
    if xb - xa < 8 or yb - ya < 8:
        return arr
    return arr[ya:yb, xa:xb]


# ---------------------------------------------------------------------------
# Feature bundle
# ---------------------------------------------------------------------------
def extract_features_from_image(img: Image.Image) -> dict[str, Any]:
    rgb = to_array(img)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask, bg_rgb, fallback = segment_foreground(rgb)
    crop_gray = crop_to_mask(gray, mask)
    return {
        "phash": phash(crop_gray),
        "dhash": dhash(crop_gray),
        "phash_full": phash(gray),
        "hist": [round(float(x), 6) for x in colour_hist(rgb, mask)],
        "dominant": dominant_colours(rgb, mask, k=6),
        "fg_ratio": round(float(mask.mean()), 4),
        "bbox": bbox_of(mask),
        "bg_rgb": [int(x) for x in bg_rgb],
        "sharpness": round(sharpness(gray), 2),
        "segmentation_fallback": fallback,
    }


def extract_features(path: Path) -> dict[str, Any]:
    return extract_features_from_image(open_image(path, ANALYSIS_SIDE))


# ---------------------------------------------------------------------------
# ORB keypoints (cached on disk per file hash)
# ---------------------------------------------------------------------------
_orb = cv2.ORB_create(nfeatures=900)


def orb_for_image(img: Image.Image) -> tuple[np.ndarray, np.ndarray | None]:
    rgb = to_array(img)
    mask, _, _ = segment_foreground(rgb)
    gray = crop_to_mask(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), mask, pad=0.08)
    scale = 600 / max(gray.shape)
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    kps, des = _orb.detectAndCompute(gray, None)
    pts = np.array([k.pt for k in kps], dtype=np.float32) if kps else np.zeros((0, 2), np.float32)
    return pts, des


def orb_features(path: Path, file_hash: str | None = None) -> tuple[np.ndarray, np.ndarray | None]:
    cache_dir = DATA_DIR / "cache" / "orb"
    if file_hash:
        f = cache_dir / f"{file_hash}.npz"
        if f.exists():
            try:
                z = np.load(f)
                return z["pts"], (z["des"] if z["des"].size else None)
            except Exception:
                pass
    pts, des = orb_for_image(open_image(path, ANALYSIS_SIDE))
    if file_hash:
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.savez(cache_dir / f"{file_hash}.npz", pts=pts, des=des if des is not None else np.zeros((0, 32), np.uint8))
    return pts, des


_bf = cv2.BFMatcher(cv2.NORM_HAMMING)


def orb_similarity(a: tuple[np.ndarray, np.ndarray | None], b: tuple[np.ndarray, np.ndarray | None]) -> float:
    pa, da = a
    pb, db = b
    if da is None or db is None or len(da) < 8 or len(db) < 8:
        return 0.0
    matches = _bf.knnMatch(da, db, k=2)
    good = [m[0] for m in matches if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
    if len(good) < 8:
        return round(min(1.0, len(good) / 40), 4)
    src = np.float32([pa[m.queryIdx] for m in good]).reshape(-1, 1, 2)
    dst = np.float32([pb[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    _, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=6.0)
    inliers = int(inl.sum()) if inl is not None else 0
    denom = max(20.0, 0.12 * min(len(da), len(db)))
    return round(min(1.0, inliers / denom), 4)


# ---------------------------------------------------------------------------
# Pairwise similarity
# ---------------------------------------------------------------------------
# Calibrated on multi-angle photographs: the chroma histogram is kept for pre-filtering and
# reporting but is too sensitive to crop/rotation to drive the decision.
NEAR_DUPLICATE_HAMMING = 8   # full-image pHash; different real products on the same card measured ≥ 14
NEAR_DUPLICATE_COLOUR = 0.80


def is_near_duplicate(fa: dict[str, Any], fb: dict[str, Any]) -> bool:
    """Near-identical shots of the same item (burst / re-export / slight reframe).

    Requires both a near-identical overall image AND agreeing product colours, so two different
    products photographed in the same set-up are not mistaken for each other.
    """
    ham = hamming(fa.get("phash_full", fa["phash"]), fb.get("phash_full", fb["phash"]))
    if ham > NEAR_DUPLICATE_HAMMING:
        return False
    return colour_similarity(key_colours(fa["dominant"]), key_colours(fb["dominant"])) >= NEAR_DUPLICATE_COLOUR


WEIGHTS = {"hist": 0.0, "colour": 0.60, "phash": 0.10, "orb": 0.30}


def pair_similarity(fa: dict[str, Any], fb: dict[str, Any], orb_a: Any = None, orb_b: Any = None) -> dict[str, float]:
    ha = np.array(fa["hist"], dtype=np.float32)
    hb = np.array(fb["hist"], dtype=np.float32)
    hist = max(0.0, float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL)))
    colour = colour_similarity(fa["dominant"], fb["dominant"])
    ham = min(hamming(fa["phash"], fb["phash"]), hamming(fa["dhash"], fb["dhash"]))
    ph = max(0.0, min(1.0, ((1 - ham / 64) - 0.5) / 0.5))
    orb = orb_similarity(orb_a, orb_b) if orb_a is not None and orb_b is not None else 0.0
    score = WEIGHTS["hist"] * hist + WEIGHTS["colour"] * colour + WEIGHTS["phash"] * ph + WEIGHTS["orb"] * orb
    near_dup = is_near_duplicate(fa, fb)
    return {"score": round(score, 4), "hist": round(hist, 4), "colour": colour, "phash": round(ph, 4),
            "orb": orb, "near_duplicate": float(near_dup)}


# ---------------------------------------------------------------------------
# Thumbnails / previews for the UI (cached, never touches the source)
# ---------------------------------------------------------------------------
def thumbnail_bytes(path: Path, size: int, fmt: str = "JPEG") -> bytes:
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}|{fmt}".encode()).hexdigest()
    cache = DATA_DIR / "cache" / "thumbs" / f"{key}.{fmt.lower()}"
    if cache.exists():
        return cache.read_bytes()
    img = open_image(path, size)
    buf = io.BytesIO()
    img.save(buf, fmt, quality=86)
    data = buf.getvalue()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return data


def encode_for_upload(path: Path, max_side: int = 2048) -> tuple[str, bytes, str]:
    """Convert any supported source (incl. HEIC) into an API-compatible PNG/JPEG payload."""
    img = open_image(path, max_side)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=94)
    return f"{path.stem}.jpg", buf.getvalue(), "image/jpeg"
