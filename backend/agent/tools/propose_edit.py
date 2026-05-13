"""`propose_edit` · diff-preview HITL gate around `write_file`.

In production agents, blindly overwriting files is a footgun.  Cursor /
Claude Code show a diff and require user approval before applying.  We
do the same with two cooperating tools:

  - `propose_edit(path, new_content)` · stages a pending edit, returns
    the unified diff to the LLM (and as a trace event to the UI).  Does
    NOT touch the file.

  - `apply_edit(token)` · commits the staged edit if the token matches.
    Whether the user approves automatically or via UI prompt is decided
    by the permission system (see `agent/permissions.py`).  By default
    `apply_edit` is set to "ask".

The pending edit is parked in a process-local store keyed by a short
token included in the diff; this lets the LLM (or the UI) commit the
edit by id without re-passing the entire content.

Usage from the LLM (good):

    1. `read_file('config.py')`              # see current state
    2. `propose_edit(path='config.py',
                     new_content=<rewritten>)`
       → returns diff + token
    3. `apply_edit(token=<token>)`           # actually writes
       → returns "ok"

Usage from the LLM (bad):

    `write_file(path='config.py', content=...)`   # bypasses the diff
"""
from __future__ import annotations

import difflib
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from langchain_core.tools import tool

from ..config import get_settings


_PENDING: "OrderedDict[str, dict]" = OrderedDict()
_PENDING_MAX = 32
_LOCK = threading.Lock()


def _put(record: dict) -> str:
    token = uuid.uuid4().hex[:10]
    with _LOCK:
        _PENDING[token] = record
        _PENDING.move_to_end(token)
        while len(_PENDING) > _PENDING_MAX:
            _PENDING.popitem(last=False)
    return token


def _take(token: str) -> dict | None:
    with _LOCK:
        return _PENDING.pop(token, None)


def _resolve(path: str) -> Path:
    """Bound writes to the configured workdir · same rule as file_ops."""
    cfg = get_settings()
    full = (cfg.workdir / path).resolve()
    if not str(full).startswith(str(cfg.workdir.resolve())):
        raise ValueError(f"path {path!r} escapes workdir")
    return full


@tool
def propose_edit(path: str, new_content: str) -> str:
    """Preview a file edit · returns a unified diff + an apply token.

    Use this BEFORE `write_file` for any non-trivial edit (config, code,
    docs).  The user sees the diff in the trace UI; you receive the diff
    text + a `token` to commit later via `apply_edit(token)`.

    Args:
        path: file path relative to the workdir (will be created if absent).
        new_content: the FULL new contents of the file.  Must be the entire
            file, not a partial diff — we compute the diff for you.

    Returns:
        A string with the unified diff and an `apply_edit token=...` hint.
        Apply the edit only after the user signals approval (via
        permission system or by saying "go").
    """
    try:
        full = _resolve(path)
    except ValueError as e:
        return f"[error] {e}"

    old = ""
    if full.exists():
        try:
            old = full.read_text(encoding="utf-8")
        except OSError as e:
            return f"[error] could not read existing file: {e}"

    diff_lines = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    ))
    if not diff_lines:
        return "[no-op] proposed content is identical to current file."

    diff = "".join(diff_lines)
    token = _put({"path": path, "new_content": new_content, "ts": time.time()})
    return (
        f"```diff\n{diff}\n```\n"
        f"To apply this edit, call apply_edit(token='{token}').  "
        f"Token is single-use; expires after {_PENDING_MAX} new proposals."
    )


@tool
def apply_edit(token: str) -> str:
    """Commit a previously-proposed edit by its token.

    Permission-gated · subject to the same `ask` / `allow` policy as
    `write_file`.  Returns the bytes-written count or an error string.
    """
    rec = _take(token)
    if rec is None:
        return f"[error] no pending edit with token {token!r} (expired or already applied)"
    full = _resolve(rec["path"])
    full.parent.mkdir(parents=True, exist_ok=True)
    try:
        full.write_text(rec["new_content"], encoding="utf-8")
    except OSError as e:
        return f"[error] write failed: {e}"
    return f"ok · wrote {len(rec['new_content'])} chars to {rec['path']}"
