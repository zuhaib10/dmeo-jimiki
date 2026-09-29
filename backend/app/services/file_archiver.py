"""Move processed raw photographs to ``raw images/completed images/<product folder>``.

Safety rules:
* only after the product has completed successfully (never in dry run)
* never overwrite — a clashing name gets a " (n)" suffix
* cross-volume moves are copy → verify SHA-256 → remove original
* the database records the new location; ``original_path`` is kept forever
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Product, SourceImage, SourceStatus, utcnow
from .audit_logger import audit
from .features import sha256_file

log = logging.getLogger("jimiki.archiver")


def unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def safe_move(src: Path, dest: Path, expected_hash: str | None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest)
    try:
        os.rename(src, dest)  # atomic on the same volume; fails rather than overwrite on Windows
        return dest
    except OSError:
        pass
    shutil.copy2(src, dest)
    if expected_hash and sha256_file(dest) != expected_hash:
        dest.unlink(missing_ok=True)
        raise IOError(f"Copy verification failed for {src.name}; original left in place")
    os.remove(src)
    return dest


def archive_product_sources(session: Session, product: Product) -> list[str]:
    """Archive every raw source of ``product`` still in the raw folder. Idempotent."""
    if not product.folder_name:
        return []
    dest_dir = settings.completed_dir / product.folder_name
    moved: list[str] = []
    for src in list(product.sources):
        if src.status == SourceStatus.ARCHIVED:
            continue
        path = Path(src.current_path)
        if not path.exists():
            audit(session, "source_missing", f"Source {src.original_filename} not found at {path}; not archived",
                  product=product, level="WARN", source_image_id=src.id)
            continue
        new_path = safe_move(path, dest_dir / path.name, src.file_hash)
        src.current_path = str(new_path)
        src.status = SourceStatus.ARCHIVED
        src.archived_at = utcnow()
        moved.append(new_path.name)
    if moved:
        audit(session, "raw_files_moved",
              f"Raw files moved to completed images/{product.folder_name}: {', '.join(moved)}",
              product=product, details={"destination": str(dest_dir), "files": moved})
    return moved


def archive_single_source(session: Session, product: Product, src: SourceImage) -> None:
    """Used when a new photograph is attached to an already-completed product."""
    if src.status == SourceStatus.ARCHIVED or not product.folder_name:
        return
    path = Path(src.current_path)
    if not path.exists():
        return
    new_path = safe_move(path, settings.completed_dir / product.folder_name / path.name, src.file_hash)
    src.current_path = str(new_path)
    src.status = SourceStatus.ARCHIVED
    src.archived_at = utcnow()
    audit(session, "raw_files_moved", f"Raw file moved to completed images/{product.folder_name}: {new_path.name}",
          product=product, source_image_id=src.id)
