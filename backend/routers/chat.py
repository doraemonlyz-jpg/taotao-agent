"""Chat endpoints · the LLM-hot routes.

  POST   /chat                    · graph backend · SSE
  POST   /chat/v2                 · harness backend · SSE
  POST   /chat/replay             · re-run a past session against either backend
  GET    /chat/replay/sessions    · list replayable session_ids
  GET    /chat/v2/tools           · harness-side tool registry
  DELETE /chat/v2/session/{sid}   · wipe a harness session

All POSTs are gated by API_KEY (when set) and rate-limited via slowapi
(when enabled).  Both /chat and /chat/v2 share the SSE wire format ·
`session` → `token`* / `trace`* → `done`.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent.config import get_settings
from agent.graph import get_graph
from agent.harness import run_harness
from agent.harness.persistence import get_store as get_harness_store
from agent.harness.tools import tool_descriptions as harness_tool_descriptions
from agent.observability import UsageCallback, event_bus
from agent.quota import enforce_user_quota
from agent.security import chat_rate_limit, enforce_session_budget, require_api_key
from agent.slash_commands import dispatch as slash_dispatch

from .shared import maybe_rate_limit

router = APIRouter()


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None
    mode: str = "act"  # "act" | "plan"


# ------------- /chat (graph backend) ---------------------------------- #
@router.post(
    "/chat",
    tags=["chat"],
    dependencies=[Depends(require_api_key), Depends(enforce_user_quota)],
)
@maybe_rate_limit(chat_rate_limit())
async def chat(request: Request, payload: ChatIn):
    sid = payload.session_id or str(uuid.uuid4())
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(400, "empty message")
    await enforce_session_budget(payload.session_id)

    graph = get_graph()
    queue = event_bus.subscribe(sid)

    # Nodes whose token stream we forward to the user as the agent's reply.
    # Internal LLM calls (planner / summarizer / critic / extractor /
    # supervisor / sub-agent intermediates) are NOT streamed.
    USER_FACING_NODES = {"executor", "writer"}

    async def run_graph():
        """Drive the graph with streaming · every chat-model token in a
        user-facing node is published as a `kind=token` event; every other
        node still publishes its existing trace events via emit().

        Uses LangGraph's astream_events v2 API for fine-grained hooks into
        every step (including individual LLM tokens)."""
        try:
            async for ev in graph.astream_events(
                {"user_input": user_text, "session_id": sid},
                {
                    "configurable": {"thread_id": sid},
                    "recursion_limit": 50,
                    "callbacks": [UsageCallback(session_id=sid)],
                },
                version="v2",
            ):
                kind = ev.get("event")
                if kind != "on_chat_model_stream":
                    continue
                meta = ev.get("metadata") or {}
                node_name = meta.get("langgraph_node", "")
                if node_name not in USER_FACING_NODES:
                    continue
                chunk = (ev.get("data") or {}).get("chunk")
                if chunk is None:
                    continue
                # AIMessageChunk.content is either str or a list of blocks
                text_pieces: list[str] = []
                content = chunk.content
                if isinstance(content, str):
                    if content:
                        text_pieces.append(content)
                elif isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            t = b.get("text") or ""
                            if t:
                                text_pieces.append(t)
                if not text_pieces:
                    continue
                event_bus.publish(sid, {
                    "ts": 0,
                    "node": node_name,
                    "kind": "token",
                    "payload": {"text": "".join(text_pieces)},
                })
        except Exception as e:
            event_bus.publish(sid, {
                "ts": 0, "node": "graph", "kind": "error",
                "payload": {"error": repr(e)},
            })
        finally:
            event_bus.publish(sid, {
                "ts": 0, "node": "graph", "kind": "_done", "payload": {"session_id": sid},
            })

    async def event_stream():
        runner = asyncio.create_task(run_graph())
        try:
            yield {"event": "session", "data": json.dumps({"session_id": sid})}
            while True:
                evt = await queue.get()
                kind = evt.get("kind")
                if kind == "_done":
                    yield {"event": "done", "data": json.dumps(evt["payload"])}
                    return
                if kind == "token":
                    yield {"event": "token", "data": json.dumps(evt["payload"])}
                    continue
                yield {"event": "trace", "data": json.dumps(evt, default=str)}
        finally:
            event_bus.unsubscribe(sid, queue)
            if not runner.done():
                runner.cancel()

    return EventSourceResponse(event_stream())


# ------------- /chat/v2 (harness backend) ----------------------------- #
@router.post(
    "/chat/v2",
    tags=["chat"],
    dependencies=[Depends(require_api_key), Depends(enforce_user_quota)],
)
@maybe_rate_limit(chat_rate_limit())
async def chat_v2(request: Request, payload: ChatIn):
    """Same wire format as /chat · runs the HARNESS implementation instead
    of the LangGraph multi-node graph.  Both endpoints share:
      - request shape (ChatIn)
      - SSE event shape (session / token / trace / done)
      - tools, memory, profile, observability bus

    Differences:
      - /chat    → 13-node LangGraph · routing decided by edges
      - /chat/v2 → single while-loop · routing decided by the LLM via tool_calls

    See docs/harness.html for the full design walkthrough."""
    sid = payload.session_id or str(uuid.uuid4())
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(400, "empty message")
    await enforce_session_budget(payload.session_id)

    # Slash commands · short-circuit before LLM
    slash = slash_dispatch(user_text, sid)
    if slash is not None:
        if isinstance(slash, dict) and slash.get("rewrite"):
            user_text = slash["rewrite"]  # rewritten prompt → continue to LLM
        else:
            reply = slash if isinstance(slash, str) else (slash.get("reply") or "(ok)")
            async def _slash_stream():
                yield {"event": "session", "data": json.dumps({"session_id": sid})}
                yield {"event": "trace", "data": json.dumps({"node": "slash", "kind": "answer", "payload": {"text": reply}})}
                yield {"event": "done", "data": json.dumps({"session_id": sid})}
            return EventSourceResponse(_slash_stream())

    plan_mode = (payload.mode == "plan")
    queue = event_bus.subscribe(sid)
    runner: asyncio.Task | None = None

    async def run_loop():
        try:
            async for _ev in run_harness(user_text, sid, plan_mode=plan_mode):
                # Loop yields are mirrored on event_bus already · the iter
                # is consumed only to drive the loop (and surface errors).
                pass
        except Exception as e:
            event_bus.publish(sid, {
                "ts": 0, "node": "harness", "kind": "error",
                "payload": {"error": repr(e)},
            })
        finally:
            event_bus.publish(sid, {
                "ts": 0, "node": "harness", "kind": "_done",
                "payload": {"session_id": sid},
            })

    async def event_stream():
        nonlocal runner
        runner = asyncio.create_task(run_loop())
        try:
            yield {"event": "session", "data": json.dumps({"session_id": sid})}
            while True:
                evt = await queue.get()
                kind = evt.get("kind")
                if kind == "_done":
                    yield {"event": "done", "data": json.dumps(evt["payload"])}
                    return
                if kind == "token":
                    yield {"event": "token", "data": json.dumps(evt["payload"])}
                    continue
                yield {"event": "trace", "data": json.dumps(evt, default=str)}
        finally:
            event_bus.unsubscribe(sid, queue)
            if runner and not runner.done():
                runner.cancel()

    return EventSourceResponse(event_stream())


# ------------- /chat/replay -------------------------------------------- #
class ReplayIn(BaseModel):
    """Replay a past turn from the JSONL trace file.

    `session_id` is the original session you want to reproduce. The first
    user_input event for that session is fetched and re-executed against
    `engine` (default `harness`). Useful for "did my prompt change break
    yesterday's conversation?" regression testing without rebuilding state.
    """

    session_id: str
    engine: str = "harness"  # "graph" | "harness"


def _trace_payload_text(payload: Any) -> str | None:
    """Extract the user-typed message from heterogenous trace payloads."""
    if not isinstance(payload, dict):
        return None
    for key in ("user_input", "user", "message", "text", "input"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _find_replay_input(session_id: str) -> str | None:
    """Scan the JSONL trace for the first user message of `session_id`."""
    path = get_settings().trace_file
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            evt = json.loads(line)
        except Exception:
            continue
        # session_id may live on the event root or inside payload
        sid = evt.get("session_id") or (evt.get("payload") or {}).get("session_id")
        if sid != session_id:
            continue
        kind = evt.get("kind", "")
        if kind not in {"user_input", "input", "start", "received"}:
            # Be lenient · take any payload that *looks* like a user message.
            txt = _trace_payload_text(evt.get("payload"))
            if txt:
                return txt
            continue
        return _trace_payload_text(evt.get("payload"))
    return None


@router.post("/chat/replay", tags=["chat"], dependencies=[Depends(require_api_key)])
@maybe_rate_limit(chat_rate_limit())
async def chat_replay(request: Request, payload: ReplayIn):
    """Replay the first user message of a past session against the chosen
    engine. Returns the same SSE stream as /chat or /chat/v2."""
    user_text = _find_replay_input(payload.session_id)
    if not user_text:
        raise HTTPException(
            404,
            f"no user input found for session {payload.session_id} in trace log",
        )
    if payload.engine not in ("graph", "harness"):
        raise HTTPException(400, "engine must be 'graph' or 'harness'")

    forwarded = ChatIn(message=user_text, session_id=None)
    if payload.engine == "harness":
        return await chat_v2(request, forwarded)
    return await chat(request, forwarded)


@router.get("/chat/replay/sessions", tags=["chat"])
def list_replayable_sessions(limit: int = 50) -> list[dict]:
    """List up to N most-recent session_ids found in the trace log,
    each with its first user message + last event timestamp."""
    path = get_settings().trace_file
    if not path.exists():
        return []
    seen: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            evt = json.loads(line)
        except Exception:
            continue
        sid = evt.get("session_id") or (evt.get("payload") or {}).get("session_id")
        if not sid:
            continue
        ts = evt.get("ts") or 0
        info = seen.setdefault(sid, {"session_id": sid, "first_user": None, "last_ts": 0})
        info["last_ts"] = max(info["last_ts"], float(ts))
        if info["first_user"] is None:
            txt = _trace_payload_text(evt.get("payload"))
            if txt:
                info["first_user"] = txt[:160]
    out = sorted(seen.values(), key=lambda x: x["last_ts"], reverse=True)
    return out[:limit]


# ------------- /chat/v2/tools + /chat/v2/session/{id} ----------------- #
@router.get("/chat/v2/tools", tags=["chat"])
def harness_tools() -> list[dict]:
    """Tool descriptions exposed by the HARNESS (vs /tools which lists
    the graph's registry)."""
    return harness_tool_descriptions()


@router.delete(
    "/chat/v2/session/{session_id}",
    tags=["chat"],
    dependencies=[Depends(require_api_key)],
)
def reset_harness_session(session_id: str) -> dict:
    """Wipe a harness session's persisted message list."""
    get_harness_store().clear(session_id)
    return {"ok": True, "session_id": session_id}
