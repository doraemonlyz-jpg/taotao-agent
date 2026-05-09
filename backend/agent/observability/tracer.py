"""Observability — JSONL persistence + an in-memory pub/sub for live SSE."""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any

from ..config import get_settings
from ..state import TraceEvent

# session_id → list of asyncio.Queue (one per active SSE listener)
_listeners: dict[str, list[asyncio.Queue]] = defaultdict(list)


class EventBus:
    """Tiny pub/sub keyed by session_id. Lets the FastAPI SSE endpoint
    subscribe to the live trace stream emitted from inside graph nodes."""

    @staticmethod
    def subscribe(session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        _listeners[session_id].append(q)
        return q

    @staticmethod
    def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
        if q in _listeners.get(session_id, []):
            _listeners[session_id].remove(q)
        if not _listeners.get(session_id):
            _listeners.pop(session_id, None)

    @staticmethod
    def publish(session_id: str, evt: TraceEvent) -> None:
        for q in _listeners.get(session_id, []):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # drop on backpressure — observability is best-effort


event_bus = EventBus()


def write_jsonl(evt: TraceEvent) -> None:
    """Append one trace event to the JSONL trace file."""
    path = get_settings().trace_file
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def emit(node: str, kind: str, payload: dict[str, Any], session_id: str | None = None) -> TraceEvent:
    """Emit a trace event from inside a node. Side effects: JSONL + SSE bus."""
    evt: TraceEvent = {
        "ts": time.time(),
        "node": node,
        "kind": kind,  # type: ignore[typeddict-item]
        "payload": payload,
    }
    write_jsonl(evt)
    if session_id:
        event_bus.publish(session_id, evt)
    return evt
