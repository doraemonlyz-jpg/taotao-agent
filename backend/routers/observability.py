"""Observability endpoints · read-only · cheap.

  GET /traces    · last N events from the JSONL trace file
  GET /usage     · token usage + USD cost (global or per-session)
  GET /usage/me  · current user's quota state (today + this month)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from agent.auth import Identity, current_identity
from agent.config import get_settings
from agent.observability import usage as usage_tracker
from agent.quota import snapshot as quota_snapshot

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


@router.get("/usage/me", tags=["observability"])
def usage_me(ident: Identity = Depends(current_identity)) -> dict:
    """Caller's own quota state · today + this month + caps + exhausted flag.

    Frontend can poll this on dashboard load to render a "78% of monthly
    quota used" progress bar.  Returns `{"enabled": false}` when the
    quota subsystem is off · UI should hide the widget in that case.
    """
    return quota_snapshot(tenant_id=ident.tenant_id, user_id=ident.user_id)
