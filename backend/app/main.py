"""JIMIKI Image Studio — FastAPI entrypoint.

Run:  uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import database, runtime
from .config import FRONTEND_DIST, settings
from .logging_setup import setup_logging
from .api import events, media, products, system, uploads
from .services.audit_logger import audit
from .services.events import bus
from .services.file_watcher import RawFolderWatcher
from .services.job_manager import manager
from .services.recovery import recover

log = logging.getLogger("jimiki.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    bus.bind_loop(asyncio.get_running_loop())
    if database.engine is None:
        database.configure()
    database.init_db()
    settings.ensure_directories()
    with database.session_scope() as s:
        audit(s, "system_started", f"JIMIKI Image Studio started — mode {settings.WORKFLOW_MODE}"
              + ("" if settings.generation_enabled else f" (DRY RUN: {settings.dry_run_reason})"))
    if os.environ.get("JIMIKI_DISABLE_WORKERS") != "1":
        watcher = RawFolderWatcher(manager.on_file_stable, polling=os.environ.get("JIMIKI_POLLING") == "1")
        runtime.watcher = watcher
        manager.watcher = watcher
        recover(manager)
        manager.start()
        watcher.start()
    log.info("Ready. Root: %s", settings.root)
    yield
    if runtime.watcher:
        runtime.watcher.stop()
    manager.stop()


app = FastAPI(title="JIMIKI Image Studio", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(products.router)
app.include_router(system.router)
app.include_router(media.router)
app.include_router(events.router)
app.include_router(uploads.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file() and FRONTEND_DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
