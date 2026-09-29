"""In-process event bus feeding the Server-Sent Events stream.

Workers run in threads; subscribers are asyncio queues owned by the event
loop, so publishing hops onto the loop with ``call_soon_threadsafe``.
Events raised inside a DB transaction are buffered on the session and only
published after commit (see ``database.py``) so the UI never refetches
state that has not been committed yet.
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        payload = json.dumps({"type": event_type, "data": data or {},
                              "ts": datetime.now(timezone.utc).isoformat()}, default=str)
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, payload)
            except RuntimeError:
                pass


def _offer(q: asyncio.Queue[str], payload: str) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(payload)


bus = EventBus()
