"""Meta endpoints · read-only or admin · cheap, no LLM calls.

  GET  /health         · liveness + featureflag inventory
  GET  /tools          · graph-side tool registry
  GET  /models         · discovery (Ollama probe + provider env)
  POST /model          · hot-swap active model · API_KEY required
  GET  /permissions    · effective permission rules
  POST /permissions/decide · user answer to a permission_request event
  GET  /hooks          · loaded hook config
  GET  /config         · settings layer chain (global → project → env)
  POST /notify         · desktop alert + SSE notification event
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent.config import SETTINGS_LAYERS, get_settings, update_runtime_model
from agent.hooks import list_hooks
from agent.mcp.client import get_status as mcp_client_status
from agent.models_catalog import auto_pair_fast, list_all as list_models_all, model_supports_tools
from agent.nodes.llm import reset_llm_cache
from agent.observability import event_bus
from agent.permissions import add_rule as add_perm_rule, list_rules as list_perm_rules
from agent.security import require_api_key
from agent.tools import tool_descriptions

router = APIRouter()

# Filled in by app.py at startup so /health can echo them back.
_FEATURE_STATUS: dict[str, Any] = {
    "security": {},
    "telemetry": {},
    "mcp": {"server": {"http_enabled": False}},
}


def set_feature_status(*, security: dict, telemetry: dict, mcp_server: dict) -> None:
    """Inject app-level startup statuses so /health can report them.
    Called once by app.py after install_security / install_telemetry / MCP build."""
    _FEATURE_STATUS["security"] = security
    _FEATURE_STATUS["telemetry"] = telemetry
    _FEATURE_STATUS["mcp"]["server"] = mcp_server


@router.get("/health", tags=["meta"])
def health() -> dict:
    cfg = get_settings()
    return {
        "ok": True,
        "model": cfg.model,
        "critic_enabled": cfg.critic_enabled,
        "guardrails_enabled": cfg.guardrails_enabled,
        "security": _FEATURE_STATUS["security"],
        "telemetry": _FEATURE_STATUS["telemetry"],
        "mcp": {
            "server": _FEATURE_STATUS["mcp"]["server"],
            "client": mcp_client_status(),
        },
    }


@router.get("/tools", tags=["meta"])
def tools() -> list[dict]:
    return tool_descriptions()


@router.get("/models", tags=["meta"])
def list_models() -> dict:
    """Probes Ollama for installed local models + reports which hosted
    providers are configured (API key present in env). Includes the
    currently-active `current` and `current_fast` ids."""
    return list_models_all()


class ModelSwitchIn(BaseModel):
    model: str | None = None
    fast_model: str | None = None


@router.post("/model", tags=["meta"], dependencies=[Depends(require_api_key)])
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
    # (defensive · shouldn't happen post-validation), drop fast back to current.
    if fast and payload.model and fast == payload.model and not model_supports_tools(payload.model):
        fast = get_settings().fast_model

    s = update_runtime_model(model=payload.model, fast_model=fast)
    reset_llm_cache()
    return {"ok": True, "model": s.model, "fast_model": s.fast_model}


@router.get("/permissions", tags=["meta"])
def perms() -> list[dict]:
    """Effective permission rules · merged project + global + runtime + default."""
    return list_perm_rules()


class PermDecide(BaseModel):
    pattern: str
    decision: str  # "allow" | "ask" | "deny"
    persist: str = "session"  # "session" | "global" | "project"
    note: str = ""


@router.post(
    "/permissions/decide", tags=["meta"], dependencies=[Depends(require_api_key)]
)
def perm_decide(p: PermDecide) -> dict:
    """User answer to a `permission_request` trace event.  Adds the rule
    so subsequent calls of the same shape don't ask again."""
    rule = add_perm_rule(p.pattern, p.decision, persist=p.persist, note=p.note)
    return {"ok": True, "rule": {"pattern": rule.pattern, "decision": rule.decision, "note": rule.note}}


@router.get("/hooks", tags=["meta"])
def hooks() -> dict:
    """Currently-loaded hook config (project + global merged)."""
    return list_hooks()


@router.get("/config", tags=["meta"])
def config_layers() -> dict:
    """Settings layer chain · global → project → os_env (later wins)."""
    cfg = get_settings()
    return {
        "layers": SETTINGS_LAYERS,
        "active": {
            "model": cfg.model,
            "fast_model": cfg.fast_model,
            "session_budget_usd": cfg.session_budget_usd,
            "tool_timeout_s": cfg.tool_timeout_s,
            "tool_result_max_chars": cfg.tool_result_max_chars,
        },
    }


class NotifyIn(BaseModel):
    title: str = "桃桃"
    message: str
    sound: bool = True
    session_id: str | None = None


@router.post("/notify", tags=["meta"], dependencies=[Depends(require_api_key)])
def notify(n: NotifyIn) -> dict:
    """Push a notification · publishes a `notification` SSE event for any
    open subscribers AND — if `terminal-notifier` is installed (mac) —
    pops a desktop alert. Used by long-running tasks to ping the user."""
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
