"""
Runs a single golden Case through one of the two backends.

We deliberately use the **internal** Python entry points instead of
HTTP'ing the running server:
  - graph:   `agent.graph.get_graph().ainvoke({...})`
  - harness: `agent.harness.run_harness(user_text, sid)`

Why? Faster (no SSE parsing), cheaper (no auth/limit), and the eval
tool can be used in CI without spinning up uvicorn.

Per-case output:
    {
      "case_id": "math.001",
      "engine":  "harness",
      "answer":  "...",
      "tools_used": ["calculator", "python"],
      "trace_event_count": 23,
      "duration_s": 4.21,
      "cost_usd": 0.00031,
      "tokens_in": 1240,
      "tokens_out": 312,
      "error": null,
    }
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

Engine = Literal["graph", "harness"]


@dataclass
class RunResult:
    case_id: str
    engine: Engine
    answer: str
    tools_used: list[str] = field(default_factory=list)
    trace_event_count: int = 0
    duration_s: float = 0.0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None


def _tools_from_events(events: list[dict]) -> list[str]:
    """Extract tool names from trace events (de-duped, ordered first-seen)."""
    seen: set[str] = set()
    out: list[str] = []
    for e in events:
        if e.get("kind") == "tool_call":
            for c in (e.get("payload") or {}).get("calls") or []:
                n = c.get("name")
                if n and n not in seen:
                    seen.add(n)
                    out.append(n)
        elif e.get("kind") == "tool" and e.get("payload", {}).get("name"):
            n = e["payload"]["name"]
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


async def _run_graph(user: str, sid: str) -> tuple[str, list[dict]]:
    from agent.graph import get_graph
    from agent.observability import event_bus, UsageCallback

    queue = event_bus.subscribe(sid)
    events: list[dict] = []
    answer: str = ""

    graph = get_graph()
    try:
        # Drain in parallel so we capture every trace event.
        async def collect():
            while True:
                evt = await queue.get()
                events.append(evt)
                if evt.get("kind") == "answer":
                    pass

        collector = asyncio.create_task(collect())
        try:
            result = await graph.ainvoke(
                {"user_input": user, "session_id": sid},
                {
                    "configurable": {"thread_id": sid},
                    "recursion_limit": 50,
                    "callbacks": [UsageCallback(session_id=sid)],
                },
            )
            # graph state has the final assistant text under various keys
            answer = (
                result.get("final_answer")
                or result.get("answer")
                or result.get("output")
                or ""
            )
            if not answer:
                # Fallback: last AI message text
                msgs = result.get("messages") or []
                for m in reversed(msgs):
                    text = getattr(m, "content", None)
                    if isinstance(text, str) and text.strip():
                        answer = text
                        break
        finally:
            await asyncio.sleep(0.05)  # let last events flush
            collector.cancel()
    finally:
        event_bus.unsubscribe(sid, queue)
    return answer, events


async def _run_harness(user: str, sid: str) -> tuple[str, list[dict]]:
    from agent.harness import run_harness
    from agent.observability import event_bus

    queue = event_bus.subscribe(sid)
    events: list[dict] = []
    answer: str = ""
    try:
        async def collect():
            while True:
                evt = await queue.get()
                events.append(evt)

        collector = asyncio.create_task(collect())
        try:
            async for ev in run_harness(user, sid):
                if isinstance(ev, dict):
                    if ev.get("kind") == "answer" and isinstance(
                        ev.get("payload", {}).get("text"), str
                    ):
                        answer = ev["payload"]["text"]
                    elif ev.get("kind") == "final" and isinstance(
                        ev.get("payload", {}).get("text"), str
                    ):
                        answer = ev["payload"]["text"]
        finally:
            await asyncio.sleep(0.05)
            collector.cancel()
    finally:
        event_bus.unsubscribe(sid, queue)
    return answer, events


async def run_case(case_id: str, user: str, engine: Engine) -> RunResult:
    """Drive one case · returns RunResult with timing + tools + answer."""
    from agent.observability import usage as usage_tracker

    sid = f"eval-{engine}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    err: str | None = None
    answer = ""
    events: list[dict] = []
    try:
        if engine == "graph":
            answer, events = await _run_graph(user, sid)
        else:
            answer, events = await _run_harness(user, sid)
    except Exception as e:
        err = repr(e)
    dur = time.perf_counter() - t0

    snap = usage_tracker.snapshot(sid)
    sess = (snap or {}).get("session") or {}

    return RunResult(
        case_id=case_id,
        engine=engine,
        answer=answer or "(no answer)",
        tools_used=_tools_from_events(events),
        trace_event_count=len(events),
        duration_s=round(dur, 3),
        cost_usd=float(sess.get("cost_usd", 0.0)),
        tokens_in=int(sess.get("input", 0)),
        tokens_out=int(sess.get("output", 0)),
        error=err,
    )


def to_dict(r: RunResult) -> dict:
    return asdict(r)
