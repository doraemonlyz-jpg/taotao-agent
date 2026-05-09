"""Wrap a `langchain_core.tools.BaseTool` with three production-grade
behaviours, so we can apply them uniformly across the registry:

  • per-call **timeout** — kill a hung tool, return a structured error
  • **LRU cache** — identical (tool, args) within the same process
    returns the previous result for free (deterministic tools only)
  • result **truncation** — never feed more than N chars back to the LLM

A tool can opt out of caching by setting `cache=False` when wrapping.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import threading
from collections import OrderedDict

from langchain_core.tools import BaseTool

from ..config import get_settings


_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 256
_CACHE_LOCK = threading.Lock()
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)


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


def _truncate(s: str, n: int) -> str:
    if not isinstance(s, str):
        s = str(s)
    if len(s) <= n:
        return s
    head = s[: n - 200]
    tail = s[-150:]
    return f"{head}\n\n…[truncated {len(s) - n} chars]…\n\n{tail}"


def safe_run_tool(tool: BaseTool, args: dict | str, *, cacheable: bool = True) -> str:
    """Run a tool with timeout + cache + truncation. Always returns str.

    `cacheable=False` for tools whose result depends on hidden state
    (file system, REPL, time-of-day search results)."""
    cfg = get_settings()
    key = _cache_key(tool.name, args) if cacheable else None
    if key:
        cached = _cache_get(key)
        if cached is not None:
            return f"[cache hit]\n{cached}"

    fut = _EXECUTOR.submit(tool.invoke, args)
    try:
        out = fut.result(timeout=cfg.tool_timeout_s)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        return f"[error] tool {tool.name!r} exceeded {cfg.tool_timeout_s:.0f}s timeout"
    except Exception as e:
        return f"[error] tool {tool.name!r} raised: {e}"

    text = out if isinstance(out, str) else str(out)
    text = _truncate(text, cfg.tool_result_max_chars)
    if key:
        _cache_put(key, text)
    return text


def cache_stats() -> dict:
    with _CACHE_LOCK:
        return {"size": len(_CACHE), "max": _CACHE_MAX}


def cache_clear() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
