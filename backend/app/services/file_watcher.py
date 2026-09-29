"""Watch ``raw images`` and hand over files only once copying has finished.

A file is considered stable when its size and mtime have not changed for
FILE_STABLE_SECONDS *and* it opens as an image. Camera transfers and slow
network copies therefore never get processed half-written.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from ..config import settings
from ..database import session_scope
from .audit_logger import audit
from .events import bus
from .features import probe_image
from .image_ingestion import is_candidate, is_registered_path, list_raw_candidates

log = logging.getLogger("jimiki.watcher")

UNREADABLE_GIVE_UP_SECONDS = 120
RESCAN_INTERVAL = 30


@dataclass
class Tracked:
    path: Path
    first_seen: float
    size: int = -1
    mtime: float = -1
    stable_since: float = 0.0
    last_error: str | None = None


class Stabilizer:
    def __init__(self, on_stable: Callable[[Path], None]) -> None:
        self._on_stable = on_stable
        self._tracked: dict[str, Tracked] = {}
        self._ignored: dict[str, tuple[int, float]] = {}  # unreadable files, keyed by (size, mtime)
        self._complete: set[str] = set()  # files known to be complete (dashboard uploads)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="stabilizer", daemon=True)
        self.last_activity = 0.0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def track(self, path: Path, announce: bool = True) -> None:
        key = str(path)
        with self._lock:
            if key in self._tracked or key in self._complete:
                return
            if key in self._ignored:
                try:
                    st = path.stat()
                    if self._ignored[key] == (st.st_size, st.st_mtime):
                        return
                except OSError:
                    return
            self._tracked[key] = Tracked(path=path, first_seen=time.time())
        self.last_activity = time.time()
        if announce:
            with session_scope() as s:
                audit(s, "file_discovered", f"File detected: {path.name}", stage="DISCOVERED",
                      details={"path": str(path)})
        bus.publish("inbox")

    def handoff(self, path: Path) -> None:
        """A file that is already complete (written atomically by an upload): skip the copy wait."""
        with self._lock:
            self._complete.add(str(path))
            self._tracked.pop(str(path), None)
        self.last_activity = time.time()
        self._on_stable(path)

    def snapshot(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [{"name": t.path.name, "size": max(0, t.size),
                     "stable_for": round(now - t.stable_since, 1) if t.stable_since else 0.0,
                     "waiting_for": "copy to finish" if not t.last_error else "file to become readable"}
                    for t in self._tracked.values()]

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._tracked)

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            with self._lock:
                items = list(self._tracked.values())
            for t in items:
                try:
                    self._check(t)
                except Exception as exc:  # noqa: BLE001
                    log.exception("stabilizer error for %s: %s", t.path, exc)

    def _drop(self, t: Tracked) -> None:
        with self._lock:
            self._tracked.pop(str(t.path), None)
        bus.publish("inbox")

    def _check(self, t: Tracked) -> None:
        now = time.time()
        try:
            st = t.path.stat()
        except FileNotFoundError:
            self._drop(t)
            return
        if st.st_size != t.size or st.st_mtime != t.mtime:
            t.size, t.mtime, t.stable_since = st.st_size, st.st_mtime, now
            self.last_activity = now
            return
        if st.st_size == 0 or now - t.stable_since < float(settings.FILE_STABLE_SECONDS):
            return
        try:
            probe_image(t.path)
        except Exception as exc:  # noqa: BLE001
            t.last_error = str(exc)
            if now - t.stable_since > UNREADABLE_GIVE_UP_SECONDS:
                with session_scope() as s:
                    audit(s, "file_unreadable", f"{t.path.name} could not be opened as an image and was skipped: {exc}",
                          level="ERROR", stage="DISCOVERED")
                with self._lock:
                    self._ignored[str(t.path)] = (t.size, t.mtime)
                self._drop(t)
            return
        self._drop(t)
        self.last_activity = time.time()
        self._on_stable(t.path)


class _Handler(FileSystemEventHandler):
    def __init__(self, stabilizer: Stabilizer) -> None:
        self.stabilizer = stabilizer

    def _maybe(self, p: str) -> None:
        path = Path(p).resolve()
        if is_candidate(path) and not is_registered_path(path):
            self.stabilizer.track(path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe(str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe(str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._maybe(str(event.dest_path))


class RawFolderWatcher:
    def __init__(self, on_stable: Callable[[Path], None], polling: bool = False) -> None:
        self.stabilizer = Stabilizer(on_stable)
        self._observer: Observer | None = None
        self._polling = polling
        self._stop = threading.Event()
        self._rescan_thread = threading.Thread(target=self._rescan_loop, name="rescan", daemon=True)
        self.error: str | None = None
        self.watching: Path | None = None

    def start(self) -> None:
        self.stabilizer.start()
        self._start_observer()
        self.scan()
        self._rescan_thread.start()

    def _start_observer(self) -> None:
        raw = settings.raw_dir
        try:
            settings.ensure_directories()
            obs = PollingObserver(timeout=2) if self._polling else Observer()
            obs.schedule(_Handler(self.stabilizer), str(raw), recursive=False)
            obs.start()
            self._observer = obs
            self.watching = raw
            self.error = None
            log.info("Watching %s", raw)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._observer = None
            log.error("Could not watch %s: %s", raw, exc)

    def restart(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
        self._start_observer()
        self.scan()

    def stop(self) -> None:
        self._stop.set()
        self.stabilizer.stop()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)

    @property
    def running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def scan(self) -> int:
        """Pick up files that arrived while the app was offline or whose events were missed."""
        n = 0
        for p in list_raw_candidates():
            if not is_registered_path(p.resolve()):
                self.stabilizer.track(p.resolve(), announce=True)
                n += 1
        return n

    def _rescan_loop(self) -> None:
        while not self._stop.wait(RESCAN_INTERVAL):
            try:
                if self.watching != settings.raw_dir or not self.running:
                    self.restart()
                else:
                    self.scan()
            except Exception as exc:  # noqa: BLE001
                log.warning("rescan failed: %s", exc)
