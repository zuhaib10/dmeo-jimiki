"""Versioned prompt templates stored in ``app/prompts/*.txt``.

File format::

    version: 2026-09-29.1
    ---
    Prompt body with {placeholders}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import PROMPTS_DIR


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str

    def render(self, **values: Any) -> str:
        return self.text.format_map(_Blank(values)).strip()


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def load_prompt(name: str) -> Prompt:
    raw = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    header, _, body = raw.partition("\n---\n")
    version = "unversioned"
    for line in header.splitlines():
        if line.lower().startswith("version:"):
            version = line.split(":", 1)[1].strip()
    return Prompt(name=name, version=f"{name}@{version}", text=body.strip())
