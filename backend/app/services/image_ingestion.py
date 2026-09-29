"""Registering stable raw photographs.

Raw photographs are opened read-only. They are never modified, re-encoded,
renamed or deleted here.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from ..config import SUPPORTED_EXTENSIONS, settings
from ..database import emit, session_scope
from ..models import SourceImage, SourceStatus
from .audit_logger import audit
from .features import extract_features, probe_image, sha256_file

log = logging.getLogger("jimiki.ingestion")

IGNORED_PREFIXES = ("~$", ".", "._")
IGNORED_SUFFIXES = (".tmp", ".part", ".crdownload", ".partial", ".download")


def is_candidate(path: Path) -> bool:
    try:
        if path.parent.resolve() != settings.raw_dir.resolve():
            return False
    except OSError:
        return False
    name = path.name
    if name.startswith(IGNORED_PREFIXES) or name.lower().endswith(IGNORED_SUFFIXES):
        return False
    return path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file()


def list_raw_candidates() -> list[Path]:
    raw = settings.raw_dir
    if not raw.exists():
        return []
    return sorted(p for p in raw.iterdir() if is_candidate(p))


def is_registered_path(path: Path) -> bool:
    with session_scope() as s:
        return s.scalar(select(SourceImage.id).where(SourceImage.current_path == str(path)).limit(1)) is not None


def register_file(path: Path) -> int | None:
    """Register a stable file. Returns the new source id if it needs feature extraction."""
    path = path.resolve()
    file_hash = sha256_file(path)
    with session_scope() as s:
        known = s.scalars(select(SourceImage).where(SourceImage.current_path == str(path))).first()
        if known is not None and known.file_hash == file_hash:
            return None  # already registered (restart / rescan)

        same = s.scalars(select(SourceImage).where(SourceImage.file_hash == file_hash,
                                                   SourceImage.status != SourceStatus.DUPLICATE)
                         .order_by(SourceImage.id)).first()
        if same is not None:
            if same.status not in (SourceStatus.ARCHIVED,) and not Path(same.current_path).exists():
                old = same.current_path
                same.current_path = str(path)
                audit(s, "source_renamed", f"Source file renamed: {Path(old).name} → {path.name}",
                      product=same.product, source_image_id=same.id, details={"from": old, "to": str(path)})
                return None
            dup_known = s.scalars(select(SourceImage).where(SourceImage.current_path == str(path),
                                                            SourceImage.status == SourceStatus.DUPLICATE)).first()
            if dup_known is not None:
                return None
            info = _safe_probe(path)
            dup = SourceImage(original_filename=path.name, original_path=str(path), current_path=str(path),
                              file_hash=file_hash, status=SourceStatus.DUPLICATE, duplicate_of_id=same.id,
                              file_size=path.stat().st_size, **info)
            s.add(dup)
            s.flush()
            owner = same.product.product_id if same.product else "an unprocessed photograph"
            audit(s, "duplicate_ignored",
                  f"Duplicate source ignored: {path.name} is byte-identical to {same.original_filename} ({owner}). "
                  "File left untouched.", product=same.product, level="WARN", source_image_id=dup.id,
                  stage="DISCOVERED")
            emit(s, "inbox")
            return None

        info = _safe_probe(path)
        src = SourceImage(original_filename=path.name, original_path=str(path), current_path=str(path),
                          file_hash=file_hash, status=SourceStatus.REGISTERED,
                          file_size=path.stat().st_size, **info)
        s.add(src)
        s.flush()
        audit(s, "file_stabilized",
              f"File stabilized and registered: {path.name} ({info.get('width')}×{info.get('height')} "
              f"{info.get('file_format')})", stage="DISCOVERED", source_image_id=src.id,
              details={"sha256": file_hash, "size": src.file_size})
        emit(s, "inbox")
        return src.id


def _safe_probe(path: Path) -> dict:
    try:
        p = probe_image(path)
        return {"width": p["width"], "height": p["height"], "file_format": p["format"], "mime_type": p["mime"]}
    except Exception:  # noqa: BLE001
        return {"file_format": path.suffix.lstrip(".").upper()}


def analyze_source(source_id: int) -> bool:
    """Extract deterministic features (hashes, colours, histogram)."""
    with session_scope() as s:
        src = s.get(SourceImage, source_id)
        if src is None or src.status != SourceStatus.REGISTERED:
            return False
        path = Path(src.current_path)
    try:
        feats = extract_features(path)
    except Exception as exc:  # noqa: BLE001
        with session_scope() as s:
            src = s.get(SourceImage, source_id)
            src.status = SourceStatus.ERROR
            src.error_message = str(exc)[:500]
            audit(s, "source_error", f"Could not analyse {src.original_filename}: {exc}", level="ERROR",
                  source_image_id=src.id, stage="ANALYZING")
            emit(s, "inbox")
        return False
    with session_scope() as s:
        src = s.get(SourceImage, source_id)
        src.features = feats
        src.perceptual_hash = feats["phash"]
        src.status = SourceStatus.ANALYZED
        colours = ", ".join(c["name"] for c in feats["dominant"][:3])
        audit(s, "source_analyzed", f"Image analysed: {src.original_filename} — colours {colours}",
              stage="ANALYZING", source_image_id=src.id, details={"phash": feats["phash"], "fg_ratio": feats["fg_ratio"]})
        emit(s, "inbox")
    return True
