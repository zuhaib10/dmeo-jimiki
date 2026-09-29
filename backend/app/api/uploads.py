"""Browser uploads.

``target=raw`` (default): product photographs are written into ``raw images``
and then follow exactly the same path as files copied there by hand
(stabilize → register → group …).

``target=reference``: model reference photographs are written into
``reference model images`` and used for future model/editorial images.

Safety:
* the file is streamed to a hidden ``.part`` temp file (ignored by the watcher)
  and only renamed into place once complete and verified as an image
* names are sanitised (no paths) and never overwrite an existing file
* byte-identical photographs already known to the studio are not saved again
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import select

from .. import runtime
from ..config import SUPPORTED_EXTENSIONS, settings
from ..database import emit, session_scope
from ..models import SourceImage, SourceStatus
from ..services.audit_logger import audit
from ..services.features import probe_image, sha256_file
from ..services.image_generator import list_reference_images

router = APIRouter(prefix="/api", tags=["uploads"])
log = logging.getLogger("jimiki.uploads")

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_FILES = 50
CHUNK = 1 << 20


def safe_filename(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).lstrip(". ")
    return base[:180] or f"upload-{uuid.uuid4().hex[:8]}.jpg"


def unique_target(folder: Path, name: str) -> Path:
    target = folder / name
    n = 1
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return target


@router.post("/uploads")
async def upload_images(files: list[UploadFile] = File(...),
                        target: str = Query("raw", pattern="^(raw|reference)$")) -> dict:
    is_ref = target == "reference"
    if not files:
        raise HTTPException(400, "No files received")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Upload at most {MAX_FILES} files at a time")
    raw = settings.reference_dir if is_ref else settings.raw_dir
    try:
        raw.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"Folder is not available: {exc}")
    existing_refs = {sha256_file(p): p.name for p in list_reference_images(raw)} if is_ref else {}

    results: list[dict] = []
    seen_hashes: dict[str, str] = {}
    for up in files:
        name = safe_filename(up.filename)
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            results.append({"filename": name, "status": "rejected",
                            "message": f"Unsupported file type {ext or '(none)'}"})
            continue

        tmp = raw / f".upload-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as out:
                while chunk := await up.read(CHUNK):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ValueError(f"File larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise ValueError("Empty file")
            try:
                info = probe_image(tmp)
            except Exception:  # noqa: BLE001
                raise ValueError("Not a readable image")
            file_hash = digest.hexdigest()

            if file_hash in seen_hashes:
                tmp.unlink(missing_ok=True)
                results.append({"filename": name, "status": "duplicate",
                                "message": f"Same photograph as {seen_hashes[file_hash]} in this upload"})
                continue
            if is_ref:
                if file_hash in existing_refs:
                    tmp.unlink(missing_ok=True)
                    results.append({"filename": name, "status": "duplicate",
                                    "message": f"Already a reference: {existing_refs[file_hash]}"})
                    continue
                target_path = unique_target(raw, name)
                os.replace(tmp, target_path)
                seen_hashes[file_hash] = target_path.name
                with session_scope() as s:
                    audit(s, "reference_uploaded", f"Model reference uploaded: {target_path.name} "
                          f"({info['width']}×{info['height']} {info['format']})", stage="REFERENCES")
                    emit(s, "system")
                results.append({"filename": name, "saved_as": target_path.name, "status": "saved", "size": size,
                                "width": info["width"], "height": info["height"]})
                continue
            with session_scope() as s:
                known = s.scalars(select(SourceImage).where(SourceImage.file_hash == file_hash,
                                                            SourceImage.status != SourceStatus.DUPLICATE)).first()
                known_desc = None
                if known is not None:
                    known_desc = known.original_filename + (f" ({known.product.product_id})" if known.product else "")
            if known_desc:
                tmp.unlink(missing_ok=True)
                results.append({"filename": name, "status": "duplicate",
                                "message": f"Already in the studio: {known_desc}"})
                continue

            dest = unique_target(raw, name)
            os.replace(tmp, dest)
            seen_hashes[file_hash] = dest.name
            with session_scope() as s:
                audit(s, "file_uploaded", f"File uploaded from dashboard: {dest.name} "
                      f"({info['width']}×{info['height']} {info['format']})", stage="DISCOVERED",
                      details={"size": size, "renamed": dest.name != name})
                emit(s, "inbox")
            if runtime.watcher is not None:
                runtime.watcher.stabilizer.handoff(dest.resolve())
            results.append({"filename": name, "saved_as": dest.name, "status": "saved", "size": size,
                            "width": info["width"], "height": info["height"]})
        except ValueError as exc:
            tmp.unlink(missing_ok=True)
            results.append({"filename": name, "status": "rejected", "message": str(exc)})
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            log.exception("upload failed for %s", name)
            results.append({"filename": name, "status": "rejected", "message": f"Could not save: {exc}"})
        finally:
            await up.close()

    saved = sum(1 for r in results if r["status"] == "saved")
    return {"saved": saved, "results": results, "folder": str(raw), "target": target}
