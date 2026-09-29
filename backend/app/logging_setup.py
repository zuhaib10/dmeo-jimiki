"""Structured (JSON lines) file logging + readable console logging."""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone

from .config import LOG_DIR, settings

_EXTRA = ("event_type", "product", "stage")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(), "level": record.levelname,
                "logger": record.name, "msg": record.getMessage()}
        for k in _EXTRA:
            v = getattr(record, k, None)
            if v is not None:
                data[k] = v
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("jimiki")
    if root.handlers:
        return
    root.setLevel(settings.LOG_LEVEL)
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "jimiki.log", maxBytes=10_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(fh)
    root.addHandler(ch)
    root.propagate = False
