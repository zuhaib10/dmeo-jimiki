"""SQLite engine, sessions and lightweight versioned migrations."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import DATA_DIR, settings
from .models import Base, Sequence, Setting
from .services.events import bus

log = logging.getLogger("jimiki.db")

engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


def configure(url: str | None = None) -> Engine:
    """Create (or recreate, for tests) the engine and session factory."""
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    if url is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{(DATA_DIR / 'jimiki.sqlite').as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    event.listen(engine, "connect", _sqlite_pragmas)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


# ---------------------------------------------------------------------------
# Deferred event publishing: events queued during a transaction are sent
# only once it commits.
# ---------------------------------------------------------------------------
def emit(session: Session, event_type: str, data: dict[str, Any] | None = None) -> None:
    session.info.setdefault("pending_events", []).append((event_type, data or {}))


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    pending = session.info.pop("pending_events", [])
    for event_type, data in pending:
        bus.publish(event_type, data)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop("pending_events", None)


@contextmanager
def session_scope() -> Iterator[Session]:
    assert SessionLocal is not None, "database.configure() not called"
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    assert SessionLocal is not None
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
def _m1_initial(conn: Any) -> None:
    Base.metadata.create_all(conn)


MIGRATIONS: list[tuple[int, str, Callable[[Any], None]]] = [
    (1, "initial schema", _m1_initial),
]


def init_db() -> None:
    assert engine is not None
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                          "description TEXT, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"))
        applied = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
        for version, desc, fn in MIGRATIONS:
            if version in applied:
                continue
            log.info("Applying migration %s: %s", version, desc)
            fn(conn)
            conn.execute(text("INSERT INTO schema_migrations (version, description) VALUES (:v, :d)"),
                         {"v": version, "d": desc})
        # Safety net for tables added to models without an explicit migration.
        existing = set(inspect(conn).get_table_names())
        missing = [t for name, t in Base.metadata.tables.items() if name not in existing]
        if missing:
            Base.metadata.create_all(conn, tables=missing)
    with session_scope() as s:
        if s.get(Sequence, "product_number") is None:
            s.add(Sequence(name="product_number", value=0))
    load_overrides()


def load_overrides() -> None:
    with session_scope() as s:
        for row in s.query(Setting).all():
            settings.update_override(row.key, row.value)
