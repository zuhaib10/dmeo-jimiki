"""Audit trail: every important workflow action is persisted and streamed to the UI."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..database import emit
from ..models import AuditEvent, Product

log = logging.getLogger("jimiki.audit")

_LEVELS = {"INFO": logging.INFO, "WARN": logging.WARNING, "ERROR": logging.ERROR}


def audit(session: Session, event_type: str, message: str, *, product: Product | None = None,
          stage: str | None = None, level: str = "INFO", details: dict[str, Any] | None = None,
          source_image_id: int | None = None) -> AuditEvent:
    ev = AuditEvent(
        product_id=product.id if product is not None else None,
        product_code=product.product_id if product is not None else None,
        source_image_id=source_image_id,
        stage=stage or (product.status if product is not None else None),
        event_type=event_type,
        level=level,
        message=message,
        details=details,
    )
    session.add(ev)
    session.flush()
    log.log(_LEVELS.get(level, logging.INFO), message,
            extra={"event_type": event_type, "product": ev.product_code, "stage": ev.stage})
    emit(session, "audit", {"id": ev.id, "product_id": ev.product_id, "event_type": event_type})
    return ev
