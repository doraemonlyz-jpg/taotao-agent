"""Structured user profile — a single JSON KV store always-injected
into the system prompt. Use for stable, high-signal facts:
  name, locale, expertise, preferences, do/don't, etc.

Differs from `LongTermMemory` (vector) in three ways:
  • No retrieval — every key is loaded every turn
  • Schemaless dict, but explicit keys (no embedding noise)
  • Single global file (not per-session)"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from ..config import get_settings


class Profile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # -------------- public API -----------------
    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._read())

    def get(self, key: str, default: Any = None) -> Any:
        return self.all().get(key, default)

    def update(self, key: str, value: Any) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data[key] = value
            self._write(data)
            return dict(data)

    def merge(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data.update(patch)
            self._write(data)
            return dict(data)

    def delete(self, key: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data.pop(key, None)
            self._write(data)
            return dict(data)

    def clear(self) -> None:
        with self._lock:
            self._write({})

    def to_prompt_block(self) -> str:
        """Render as a compact bullet-list for injection into prompt."""
        data = self.all()
        if not data:
            return ""
        lines = [f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in data.items()]
        return "[User profile]\n" + "\n".join(lines)


@lru_cache(maxsize=1)
def get_profile() -> Profile:
    cfg = get_settings()
    return Profile(cfg.workdir.parent / "profile.json")
