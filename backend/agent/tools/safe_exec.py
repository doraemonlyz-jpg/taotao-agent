"""Wrap a `langchain_core.tools.BaseTool` with five production-grade
behaviours, so we can apply them uniformly across the registry:

  • per-call **timeout** — kill a hung tool, return a structured error
  • **LRU cache** — identical (tool, args) within the same process
    returns the previous result for free (deterministic tools only)
  • result **truncation** — never feed more than N chars back to the LLM
  • result **offset-resume** — every truncated result is parked in a
    process-local store; the model can ask for `read_tool_result(id, offset)`
    to pull the next slice without re-running the tool
  • **permission gate** — destructive tools require an approved policy
    (see `agent/permissions.py`); otherwise raise PermissionRequired so
    the harness can pause and ask the user
  • **hook fanout** — fires `pre_tool_use` / `post_tool_use` hooks
    (see `agent/hooks.py`) so users can plug lint/fmt/git/notify scripts
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

from langchain_core.tools import BaseTool

from ..config import get_settings
from ..hooks import fire as fire_hook
from ..permissions import PermissionRequired, gate as permission_gate


_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 256
_CACHE_LOCK = threading.Lock()
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)

# Offset-resume store · LRU of full untrunctated tool outputs.
# Keyed by a short id we put in the truncation marker.  The model can call
# `read_tool_result(id, offset, limit)` to walk the rest.
_RESULT_STORE: "OrderedDict[str, dict]" = OrderedDict()
_RESULT_MAX = 64


def _cache_key(name: str, args: dict | str) -> str:
    raw = json.dumps({"n": name, "a": args}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    return None


def _cache_put(key: str, value: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def _store_full_result(name: str, full: str) -> str:
    """Park `full` in the result-store, return its short id."""
    rid = uuid.uuid4().hex[:10]
    with _CACHE_LOCK:
        _RESULT_STORE[rid] = {"tool": name, "text": full, "ts": time.time()}
        _RESULT_STORE.move_to_end(rid)
        while len(_RESULT_STORE) > _RESULT_MAX:
            _RESULT_STORE.popitem(last=False)
    return rid


def get_stored_result(rid: str, offset: int = 0, limit: int = 4000) -> dict:
    """Public · used by the `read_tool_result` tool."""
    with _CACHE_LOCK:
        rec = _RESULT_STORE.get(rid)
    if not rec:
        return {"ok": False, "error": f"no stored result with id {rid!r}"}
    text = rec["text"]
    total = len(text)
    if offset < 0:
        offset = 0
    end = min(total, offset + max(1, limit))
    return {
        "ok": True, "tool": rec["tool"], "id": rid,
        "offset": offset, "next_offset": end if end < total else None,
        "total": total, "chunk": text[offset:end],
    }


def _truncate(s: str, n: int, *, name: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    if len(s) <= n:
        return s
    rid = _store_full_result(name, s)
    head = s[: max(0, n - 260)]
    tail = s[-150:]
    marker = (
        f"\n\n…[truncated {len(s) - n} chars · resume with "
        f"read_tool_result(id='{rid}', offset={len(head)})]…\n\n"
    )
    return f"{head}{marker}{tail}"


def safe_run_tool(
    tool: BaseTool, args: dict | str,
    *, cacheable: bool = True, session_id: str = "",
    skip_permission: bool = False,
) -> str:
    """Run a tool with timeout + cache + truncation + permission + hooks.

    Always returns str. May raise `permissions.PermissionRequired` (the
    harness catches this and asks the user); pass `skip_permission=True`
    to bypass the gate (used after the user has approved out-of-band).

    `cacheable=False` for tools whose result depends on hidden state
    (file system, REPL, time-of-day search results)."""
    cfg = get_settings()
    name = tool.name

    if not skip_permission:
        try:
            permission_gate(name, args)
        except PermissionRequired:
            raise
        except PermissionError as e:
            return f"[denied] {e}"

    fire_hook("pre_tool_use", session_id=session_id, tool_name=name, args=args)

    key = _cache_key(name, args) if cacheable else None
    if key:
        cached = _cache_get(key)
        if cached is not None:
            fire_hook("post_tool_use", session_id=session_id, tool_name=name,
                      args=args, result_preview=cached[:240])
            return f"[cache hit]\n{cached}"

    def _run() -> str:
        try:
            return tool.invoke(args)
        except NotImplementedError:
            # MCP-client tools (and other adapters) sometimes only
            # implement async invocation. Spin up a private loop in this
            # worker thread so we don't fight whatever loop the caller
            # has open.
            return asyncio.run(tool.ainvoke(args))

    fut = _EXECUTOR.submit(_run)
    try:
        out = fut.result(timeout=cfg.tool_timeout_s)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        text = f"[error] tool {name!r} exceeded {cfg.tool_timeout_s:.0f}s timeout"
        fire_hook("post_tool_use", session_id=session_id, tool_name=name,
                  args=args, result_preview=text)
        return text
    except Exception as e:
        text = f"[error] tool {name!r} raised: {e}"
        fire_hook("post_tool_use", session_id=session_id, tool_name=name,
                  args=args, result_preview=text)
        return text

    # MCP tools (langchain-mcp-adapters) return a list of content blocks
    # like [{"type": "text", "text": "...", "id": "lc_..."}]. Flatten to
    # plain text so the downstream LLM doesn't see noisy JSON · keep the
    # raw string for everything else.
    if isinstance(out, list) and out and all(isinstance(b, dict) for b in out):
        parts = [b.get("text", "") for b in out if b.get("type") == "text"]
        if parts:
            out = "\n".join(parts)

    text = out if isinstance(out, str) else str(out)
    text = _truncate(text, cfg.tool_result_max_chars, name=name)
    if key:
        _cache_put(key, text)
    fire_hook("post_tool_use", session_id=session_id, tool_name=name,
              args=args, result_preview=text[:240])
    return text


def cache_stats() -> dict:
    with _CACHE_LOCK:
        return {"size": len(_CACHE), "max": _CACHE_MAX,
                "store_size": len(_RESULT_STORE), "store_max": _RESULT_MAX}


def cache_clear() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _RESULT_STORE.clear()
