"""FastAPI surface — POST /chat (SSE), GET /traces, GET/POST/DELETE /memory."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent.config import get_settings, update_runtime_model
from agent.graph import get_graph
from agent.harness import run_harness
from agent.harness.persistence import get_store as get_harness_store
from agent.harness.tools import tool_descriptions as harness_tool_descriptions
from agent.memory import get_memory, get_profile, get_reflections, list_skills
from agent.models_catalog import auto_pair_fast, list_all as list_models_all, model_supports_tools
from agent.nodes.llm import reset_llm_cache
from agent.observability import UsageCallback, event_bus, usage as usage_tracker, write_jsonl
from agent.tools import tool_descriptions

app = FastAPI(title="Agent Demo", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- /health
@app.get("/health")
def health() -> dict:
    cfg = get_settings()
    return {
        "ok": True,
        "model": cfg.model,
        "critic_enabled": cfg.critic_enabled,
        "guardrails_enabled": cfg.guardrails_enabled,
    }


# ---------------------------------------------------------------- /tools
@app.get("/tools")
def tools() -> list[dict]:
    return tool_descriptions()


# ---------------------------------------------------------------- /models (discovery + switch)
@app.get("/models")
def list_models() -> dict:
    """Probes Ollama for installed local models + reports which hosted
    providers are configured (API key present in env). Includes the
    currently-active `current` and `current_fast` ids."""
    return list_models_all()


class ModelSwitchIn(BaseModel):
    model: str | None = None
    fast_model: str | None = None


@app.post("/model")
def switch_model(payload: ModelSwitchIn) -> dict:
    """Hot-swap the active model. If `fast_model` is omitted, it's
    auto-paired (Ollama → same model; hosted → that provider's cheap tier).
    Clears the LLM build cache so the next graph turn rebuilds against
    the new pick — no backend restart needed."""
    if not payload.model and not payload.fast_model:
        raise HTTPException(400, "model or fast_model required")

    # The "big" model runs the executor and every sub-agent — both call
    # `bind_tools(...)`. Reject picks that we know don't support tools so the
    # graph can't enter a half-broken state at runtime (Ollama R1, Gemma, …).
    if payload.model and not model_supports_tools(payload.model):
        raise HTTPException(
            422,
            f"{payload.model} doesn't support tool calling, so it can't run the "
            "executor or sub-agents. Pick it as `fast_model` instead — router / "
            "critic / extractor only need structured output.",
        )

    fast = payload.fast_model
    if payload.model and not fast:
        fast = auto_pair_fast(payload.model)

    # If the picked big model auto-paired to itself but isn't tool-capable
    # (shouldn't happen post-validation, but defensive), drop fast back to the
    # current value rather than poisoning the fast slot.
    if fast and payload.model and fast == payload.model and not model_supports_tools(payload.model):
        fast = get_settings().fast_model

    s = update_runtime_model(model=payload.model, fast_model=fast)
    reset_llm_cache()
    return {"ok": True, "model": s.model, "fast_model": s.fast_model}


# ---------------------------------------------------------------- /chat (SSE)
class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/chat")
async def chat(payload: ChatIn):
    sid = payload.session_id or str(uuid.uuid4())
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(400, "empty message")

    graph = get_graph()
    queue = event_bus.subscribe(sid)

    # Nodes whose token stream we forward to the user as the agent's reply.
    # (Internal LLM calls — planner / summarizer / critic / extractor /
    # supervisor / sub-agent intermediates — are NOT streamed.)
    USER_FACING_NODES = {"executor", "writer"}

    async def run_graph():
        """Drive the graph with streaming: every chat-model token in a
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
                    # streamed LLM token from a user-facing node
                    yield {"event": "token", "data": json.dumps(evt["payload"])}
                    continue
                yield {"event": "trace", "data": json.dumps(evt, default=str)}
        finally:
            event_bus.unsubscribe(sid, queue)
            if not runner.done():
                runner.cancel()

    return EventSourceResponse(event_stream())


# ---------------------------------------------------------------- /chat/v2 (HARNESS · SSE)
@app.post("/chat/v2")
async def chat_v2(payload: ChatIn):
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

    queue = event_bus.subscribe(sid)
    runner: asyncio.Task | None = None

    async def run_loop():
        try:
            async for _ev in run_harness(user_text, sid):
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


# ---------------------------------------------------------------- /chat/v2/tools
@app.get("/chat/v2/tools")
def harness_tools() -> list[dict]:
    """Tool descriptions exposed by the HARNESS (vs /tools which lists
    the graph's registry)."""
    return harness_tool_descriptions()


# ---------------------------------------------------------------- /chat/v2/sessions
@app.delete("/chat/v2/session/{session_id}")
def reset_harness_session(session_id: str) -> dict:
    """Wipe a harness session's persisted message list."""
    get_harness_store().clear(session_id)
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------- /traces
@app.get("/traces")
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


# ---------------------------------------------------------------- /usage
@app.get("/usage")
def usage(session_id: str | None = None) -> dict:
    """Token usage + USD cost.
    - global  : all tokens since this backend process started
    - session : tokens spent inside the given session_id (optional)
    """
    return usage_tracker.snapshot(session_id)


# ---------------------------------------------------------------- /memory
class MemoryIn(BaseModel):
    text: str
    kind: str = "fact"


@app.get("/memory")
def list_memory() -> list[dict]:
    return get_memory().list_all(limit=100)


@app.post("/memory")
def add_memory(m: MemoryIn) -> dict:
    mid = get_memory().remember(m.text, kind=m.kind)
    return {"id": mid}


@app.delete("/memory")
def clear_memory() -> dict:
    get_memory().clear()
    return {"ok": True}


# ---------------------------------------------------------------- /reflections
@app.get("/reflections")
def list_reflections() -> list[dict]:
    return get_reflections().list_all(limit=100)


@app.delete("/reflections")
def clear_reflections() -> dict:
    get_reflections().clear()
    return {"ok": True}


# ---------------------------------------------------------------- /profile
class ProfileIn(BaseModel):
    key: str
    value: Any


@app.get("/profile")
def read_profile() -> dict:
    return get_profile().all()


@app.put("/profile")
def update_profile(p: ProfileIn) -> dict:
    return get_profile().update(p.key, p.value)


@app.delete("/profile/{key}")
def delete_profile_key(key: str) -> dict:
    return get_profile().delete(key)


@app.delete("/profile")
def clear_profile() -> dict:
    get_profile().clear()
    return {"ok": True}


# ---------------------------------------------------------------- /skills
@app.get("/skills")
def list_skills_endpoint() -> list[dict]:
    return [
        {
            "name": s.name,
            "description": s.description,
            "when_to_use": s.when_to_use,
            "body": s.body,
            "path": str(s.path),
        }
        for s in list_skills()
    ]


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
