"""Observability endpoints · read-only · cheap.

  GET /traces  · last N events from the JSONL trace file
  GET /usage   · token usage + USD cost (global or per-session)
"""
from __future__ import annotations

import json

from fastapi import APIRouter

from agent.config import get_settings
from agent.observability import usage as usage_tracker

router = APIRouter()


@router.get("/traces", tags=["observability"])
def traces(limit: int = 200) -> list[dict]:
    """Return the last N events from the JSONL trace file."""
    path = get_settings().trace_file
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


@router.get("/usage", tags=["observability"])
def usage(session_id: str | None = None) -> dict:
    """Token usage + USD cost.
    - global  : all tokens since this backend process started
    - session : tokens spent inside the given session_id (optional)
    """
    return usage_tracker.snapshot(session_id)
