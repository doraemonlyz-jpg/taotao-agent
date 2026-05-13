"""FastAPI surface — POST /chat (SSE), GET /traces, GET/POST/DELETE /memory."""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent.config import SETTINGS_LAYERS, get_settings, update_runtime_model
from agent.graph import get_graph
from agent.harness import run_harness
from agent.harness.persistence import get_store as get_harness_store
from agent.harness.tools import tool_descriptions as harness_tool_descriptions
from agent.memory import get_memory, get_profile, get_reflections, list_skills
from agent.models_catalog import auto_pair_fast, list_all as list_models_all, model_supports_tools
from agent.nodes.llm import reset_llm_cache
from agent.observability import (
    UsageCallback,
    event_bus,
    install_telemetry,
    usage as usage_tracker,
    write_jsonl,
)
from agent.mcp.client import get_status as mcp_client_status
from agent.mcp.server import build_mcp_server
from agent.permissions import add_rule as add_perm_rule, list_rules as list_perm_rules
from agent.hooks import list_hooks
from agent.slash_commands import dispatch as slash_dispatch
from agent.security import (
    chat_rate_limit,
    enforce_session_budget,
    install_security,
    require_api_key,
)
from agent.tools import tool_descriptions

# --- MCP server build (must happen pre-app so its lifespan can attach) ---
MCP_STATUS: dict = {"http_enabled": False, "exposed_tools": []}
_mcp_instance = None
if os.environ.get("MCP_HTTP_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"):
    try:
        _mcp_instance = build_mcp_server()
        MCP_STATUS = {
            "http_enabled": True,
            "endpoint": "/mcp",
            "transport": "streamable_http",
            "exposed_tools": [t.name for t in _mcp_instance._tool_manager.list_tools()],
        }
    except Exception as e:  # pragma: no cover · degrade gracefully
        MCP_STATUS = {"http_enabled": False, "error": repr(e)}


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Start FastMCP's StreamableHTTP session manager.

    Mounted ASGI sub-apps don't inherit the parent's lifespan events, so
    FastMCP's task group never starts and every request 500s with
    'Task group is not initialized'. We bridge it here.
    """
    if _mcp_instance is not None:
        async with _mcp_instance.session_manager.run():
            yield
    else:
        yield


app = FastAPI(
    title="桃桃 Agent · taotao-agent",
    version="0.2.0",
    description=(
        "Production-shape Agent demo. Two interchangeable backends:\n"
        "- `POST /chat`     · LangGraph 13-node graph\n"
        "- `POST /chat/v2`  · Harness while-loop (Claude-Code style)\n\n"
        "Both share SSE wire format, tool registry, memory, and observability.\n"
        "MCP-compatible · `/mcp` exposes whitelisted tools to Claude Desktop, Cursor, and other MCP clients."
    ),
    lifespan=_lifespan,
)

# CORS · default to "*" for dev demo. Tighten via ALLOWED_ORIGINS env when
# you put this behind a real domain (comma-separated list).
_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)

# Auth + rate-limit + sentry · all env-gated. See backend/agent/security.py.
SECURITY_STATUS = install_security(app)
LIMITER = getattr(app.state, "limiter", None)

# OpenTelemetry + Prometheus · auto-instruments FastAPI + httpx.
# /metrics endpoint is registered here. Tools / sub-agents / LLM calls
# get hand-instrumented via tool_span / subagent_span / llm_span.
TELEMETRY_STATUS = install_telemetry(app)

# Mount the MCP streamable-http app at /mcp. Endpoint: POST/GET http://host:8000/mcp/
if _mcp_instance is not None:
    app.mount("/mcp", _mcp_instance.streamable_http_app())


def _maybe_rate_limit(limit_str: str):
    """Decorator helper · applies slowapi limit only when limiter is on.
    Lets the same code path work in dev (no slowapi) and prod."""
    def _wrap(fn):
        if LIMITER is None:
            return fn
        return LIMITER.limit(limit_str)(fn)
    return _wrap


# ---------------------------------------------------------------- /health
@app.get("/health", tags=["meta"])
def health() -> dict:
    cfg = get_settings()
    return {
        "ok": True,
        "model": cfg.model,
        "critic_enabled": cfg.critic_enabled,
        "guardrails_enabled": cfg.guardrails_enabled,
        "security": SECURITY_STATUS,
        "telemetry": TELEMETRY_STATUS,
        "mcp": {
            "server": MCP_STATUS,
            "client": mcp_client_status(),
        },
    }


# ---------------------------------------------------------------- /tools
@app.get("/tools", tags=["meta"])
def tools() -> list[dict]:
    return tool_descriptions()


# ---------------------------------------------------------------- /models (discovery + switch)
@app.get("/models", tags=["meta"])
def list_models() -> dict:
    """Probes Ollama for installed local models + reports which hosted
    providers are configured (API key present in env). Includes the
    currently-active `current` and `current_fast` ids."""
    return list_models_all()


class ModelSwitchIn(BaseModel):
    model: str | None = None
    fast_model: str | None = None


@app.post("/model", tags=["meta"], dependencies=[Depends(require_api_key)])
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
    mode: str = "act"  # "act" | "plan"


@app.post("/chat", tags=["chat"], dependencies=[Depends(require_api_key)])
@_maybe_rate_limit(chat_rate_limit())
async def chat(request: Request, payload: ChatIn):
    sid = payload.session_id or str(uuid.uuid4())
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(400, "empty message")
    await enforce_session_budget(payload.session_id)

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
@app.post("/chat/v2", tags=["chat"], dependencies=[Depends(require_api_key)])
@_maybe_rate_limit(chat_rate_limit())
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


# ---------------------------------------------------------------- /chat/replay
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
        # Only the first "user-ish" event counts; later events are agent.
        kind = evt.get("kind", "")
        if kind not in {"user_input", "input", "start", "received"}:
            # Be lenient · take any payload that *looks* like a user message.
            txt = _trace_payload_text(evt.get("payload"))
            if txt:
                return txt
            continue
        return _trace_payload_text(evt.get("payload"))
    return None


@app.post("/chat/replay", tags=["chat"], dependencies=[Depends(require_api_key)])
@_maybe_rate_limit(chat_rate_limit())
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


@app.get("/chat/replay/sessions", tags=["chat"])
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


# ---------------------------------------------------------------- /chat/v2/tools
@app.get("/chat/v2/tools", tags=["chat"])
def harness_tools() -> list[dict]:
    """Tool descriptions exposed by the HARNESS (vs /tools which lists
    the graph's registry)."""
    return harness_tool_descriptions()


# ---------------------------------------------------------------- /chat/v2/sessions
@app.delete(
    "/chat/v2/session/{session_id}",
    tags=["chat"],
    dependencies=[Depends(require_api_key)],
)
def reset_harness_session(session_id: str) -> dict:
    """Wipe a harness session's persisted message list."""
    get_harness_store().clear(session_id)
    return {"ok": True, "session_id": session_id}





# ---------------------------------------------------------------- /permissions
@app.get("/permissions", tags=["meta"])
def perms() -> list[dict]:
    """Effective permission rules · merged project + global + runtime + default."""
    return list_perm_rules()


class PermDecide(BaseModel):
    pattern: str
    decision: str  # "allow" | "ask" | "deny"
    persist: str = "session"  # "session" | "global" | "project"
    note: str = ""


@app.post(
    "/permissions/decide", tags=["meta"], dependencies=[Depends(require_api_key)]
)
def perm_decide(p: PermDecide) -> dict:
    """User answer to a `permission_request` trace event.  Adds the rule
    so subsequent calls of the same shape don't ask again."""
    rule = add_perm_rule(p.pattern, p.decision, persist=p.persist, note=p.note)
    return {"ok": True, "rule": {"pattern": rule.pattern, "decision": rule.decision, "note": rule.note}}


# ---------------------------------------------------------------- /hooks
@app.get("/hooks", tags=["meta"])
def hooks() -> dict:
    """Currently-loaded hook config (project + global merged)."""
    return list_hooks()




# ---------------------------------------------------------------- /config
@app.get("/config", tags=["meta"])
def config_layers() -> dict:
    """Settings layer chain · global → project → os_env (later wins)."""
    cfg = get_settings()
    return {
        "layers": SETTINGS_LAYERS,
        "active": {
            "model": cfg.model, "fast_model": cfg.fast_model,
            "session_budget_usd": cfg.session_budget_usd,
            "tool_timeout_s": cfg.tool_timeout_s,
            "tool_result_max_chars": cfg.tool_result_max_chars,
        },
    }


# ---------------------------------------------------------------- /notify
class NotifyIn(BaseModel):
    title: str = "桃桃"
    message: str
    sound: bool = True
    session_id: str | None = None


@app.post("/notify", tags=["meta"], dependencies=[Depends(require_api_key)])
def notify(n: NotifyIn) -> dict:
    """Push a notification · publishes a `notification` SSE event for any
    open subscribers AND — if `terminal-notifier` is installed (mac) —
    pops a desktop alert. Used by long-running tasks to ping the user."""
    import shutil
    import subprocess
    if n.session_id:
        event_bus.publish(n.session_id, {
            "ts": 0, "node": "notify", "kind": "notification",
            "payload": {"title": n.title, "message": n.message},
        })
    desktop_ok = False
    tn = shutil.which("terminal-notifier")
    if tn:
        try:
            subprocess.Popen([
                tn, "-title", n.title, "-message", n.message,
                *(["-sound", "default"] if n.sound else []),
            ])
            desktop_ok = True
        except Exception:
            desktop_ok = False
    return {"ok": True, "desktop": desktop_ok}

# ---------------------------------------------------------------- /traces
@app.get("/traces", tags=["observability"])
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
@app.get("/usage", tags=["observability"])
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


@app.get("/memory", tags=["memory"])
def list_memory() -> list[dict]:
    return get_memory().list_all(limit=100)


@app.post("/memory", tags=["memory"], dependencies=[Depends(require_api_key)])
def add_memory(m: MemoryIn) -> dict:
    mid = get_memory().remember(m.text, kind=m.kind)
    return {"id": mid}


@app.delete("/memory", tags=["memory"], dependencies=[Depends(require_api_key)])
def clear_memory() -> dict:
    get_memory().clear()
    return {"ok": True}


class PruneIn(BaseModel):
    max_keep: int | None = None
    drop_fraction: float | None = None
    half_life_days: float | None = None
    dry_run: bool = False


@app.post("/memory/prune", tags=["memory"], dependencies=[Depends(require_api_key)])
def prune_memory(p: PruneIn) -> dict:
    """Drop low-value memories by composite recency+usage score.

    All knobs default to env-derived settings (AGENT_MEM_*).  Pass
    `dry_run: true` to preview what would go without deleting.
    """
    return get_memory().prune(
        max_keep=p.max_keep,
        drop_fraction=p.drop_fraction,
        half_life_days=p.half_life_days,
        dry_run=p.dry_run,
    )


# ---------------------------------------------------------------- /reflections
@app.get("/reflections", tags=["memory"])
def list_reflections() -> list[dict]:
    return get_reflections().list_all(limit=100)


@app.delete(
    "/reflections", tags=["memory"], dependencies=[Depends(require_api_key)]
)
def clear_reflections() -> dict:
    get_reflections().clear()
    return {"ok": True}


# ---------------------------------------------------------------- /profile
class ProfileIn(BaseModel):
    key: str
    value: Any


@app.get("/profile", tags=["memory"])
def read_profile() -> dict:
    return get_profile().all()


@app.put("/profile", tags=["memory"], dependencies=[Depends(require_api_key)])
def update_profile(p: ProfileIn) -> dict:
    return get_profile().update(p.key, p.value)


@app.delete(
    "/profile/{key}", tags=["memory"], dependencies=[Depends(require_api_key)]
)
def delete_profile_key(key: str) -> dict:
    return get_profile().delete(key)


@app.delete(
    "/profile", tags=["memory"], dependencies=[Depends(require_api_key)]
)
def clear_profile() -> dict:
    get_profile().clear()
    return {"ok": True}


# ---------------------------------------------------------------- /skills
@app.get("/skills", tags=["memory"])
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
