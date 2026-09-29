"""Read-only image delivery (sources are converted to cached JPEG previews; never modified)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_session
from ..models import GeneratedImage, SourceImage
from ..services.features import thumbnail_bytes

router = APIRouter(prefix="/api/media", tags=["media"])
CACHE = {"Cache-Control": "private, max-age=300"}


def _preview(path: Path, size: int | None) -> Response:
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return Response(thumbnail_bytes(path, max(64, min(size or 1600, 2400))), media_type="image/jpeg", headers=CACHE)


@router.get("/source/{sid}")
def source(sid: int, size: int | None = None, s: Session = Depends(get_session)) -> Response:
    src = s.get(SourceImage, sid)
    if src is None:
        raise HTTPException(404)
    return _preview(Path(src.current_path), size)


@router.get("/generated/{gid}/{fmt}")
def generated(gid: int, fmt: str, size: int | None = None, s: Session = Depends(get_session)) -> Response:
    gi = s.get(GeneratedImage, gid)
    if gi is None:
        raise HTTPException(404)
    path = Path(gi.webp_path if fmt == "webp" else gi.png_path or "")
    if size:
        return _preview(path, size)
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="image/webp" if fmt == "webp" else "image/png", filename=path.name,
                        content_disposition_type="inline")


@router.get("/reference/{name}")
def reference(name: str, size: int | None = None) -> Response:
    base = settings.reference_dir.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise HTTPException(400, "Invalid path")
    return _preview(path, size)
