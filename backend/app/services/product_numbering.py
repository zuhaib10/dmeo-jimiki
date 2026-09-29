"""Permanent product identity.

``product_number`` comes from a monotonic counter incremented with a single
atomic ``UPDATE … RETURNING`` inside the caller's transaction, so concurrent
jobs can never receive the same number and numbers are never reused (even if
a product is later removed). Unique constraints on ``products`` back this up.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

PREFIX = "JMK"


def allocate_product_number(session: Session) -> int:
    value = session.execute(
        text("UPDATE sequences SET value = value + 1 WHERE name = 'product_number' RETURNING value")
    ).scalar_one()
    return int(value)


def format_product_id(number: int) -> str:
    return f"{PREFIX}-{number:06d}"


def peek_next_numbers(session: Session, count: int) -> list[int]:
    """Proposed numbers for a dry run — nothing is consumed."""
    current = session.execute(text("SELECT value FROM sequences WHERE name = 'product_number'")).scalar() or 0
    return [int(current) + i for i in range(1, count + 1)]
