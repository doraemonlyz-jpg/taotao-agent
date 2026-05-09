"""Token-usage accounting.

Anthropic / OpenAI / Gemini don't expose 'remaining account credits' over
the API, so we do the next-best thing: count every token spent by this
backend instance, group by session_id, and convert to USD using a
hard-coded pricing table.

Uses a LangChain `BaseCallbackHandler` so a single hook captures every
LLM call inside the graph — including planner, critic, supervisor, and
sub-agents — without touching individual nodes."""
from __future__ import annotations

from collections import defaultdict
from threading import Lock
from time import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from ..config import get_settings

# --- USD per 1 M tokens (May 2026 published rates) -------------------------
PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    # Anthropic
    "anthropic:claude-sonnet-4-5-20250929": {"input": 3.0,  "output": 15.0, "cache_creation": 3.75, "cache_read": 0.30},
    "anthropic:claude-sonnet-4-5":           {"input": 3.0,  "output": 15.0, "cache_creation": 3.75, "cache_read": 0.30},
    "anthropic:claude-opus-4-5":             {"input": 15.0, "output": 75.0, "cache_creation": 18.75, "cache_read": 1.50},
    "anthropic:claude-haiku-4-5":            {"input": 0.80, "output": 4.0,  "cache_creation": 1.0,  "cache_read": 0.08},
    # OpenAI
    "openai:gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai:gpt-4o":      {"input": 2.50, "output": 10.0},
    "openai:gpt-4.1":     {"input": 2.00, "output": 8.00},
    # Gemini
    "google_genai:gemini-2.5-pro":   {"input": 1.25, "output": 10.0},
    "google_genai:gemini-2.5-flash": {"input": 0.075, "output": 0.30},
}

# Local models cost $0 (you're paying for electricity, not API tokens).
# Anything matching one of these prefixes is priced at zero — handy for
# Ollama, LM Studio, vLLM, llama.cpp, MLX, etc.
_LOCAL_PREFIXES: tuple[str, ...] = ("ollama:", "lmstudio:", "local:", "llamacpp:", "mlx:")


def _is_local(model: str | None) -> bool:
    return bool(model) and any(model.startswith(p) for p in _LOCAL_PREFIXES)

_ZERO = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0, "calls": 0}

_lock = Lock()
_global = dict(_ZERO)
_global["since"] = time()
_per_session: dict[str, dict[str, int]] = defaultdict(lambda: dict(_ZERO))


def _add_locked(target: dict[str, int], inp: int, out: int, cc: int, cr: int) -> None:
    target["input"] += inp
    target["output"] += out
    target["cache_creation"] += cc
    target["cache_read"] += cr
    target["calls"] += 1


def add(usage_metadata: dict | None, session_id: str | None) -> None:
    """LangChain v0.3 standard usage_metadata shape:
       {input_tokens, output_tokens, total_tokens,
        input_token_details: {cache_creation, cache_read}, ...}"""
    if not usage_metadata:
        return
    inp = int(usage_metadata.get("input_tokens", 0) or 0)
    out = int(usage_metadata.get("output_tokens", 0) or 0)
    details = usage_metadata.get("input_token_details") or {}
    cc = int(details.get("cache_creation", 0) or 0)
    cr = int(details.get("cache_read", 0) or 0)

    with _lock:
        _add_locked(_global, inp, out, cc, cr)
        if session_id:
            _add_locked(_per_session[session_id], inp, out, cc, cr)


def estimate_cost(counts: dict[str, int], model: str | None = None) -> float:
    m = model or get_settings().model
    if _is_local(m):
        return 0.0
    p = PRICING_PER_MTOK.get(m, {})
    if not p:
        return 0.0
    cost_micro = (
        counts.get("input", 0)          * p.get("input", 0)
        + counts.get("output", 0)         * p.get("output", 0)
        + counts.get("cache_creation", 0) * p.get("cache_creation", 0)
        + counts.get("cache_read", 0)     * p.get("cache_read", 0)
    )
    return round(cost_micro / 1_000_000, 6)


def session_cost(session_id: str) -> float:
    if not session_id:
        return 0.0
    with _lock:
        s = dict(_per_session.get(session_id, dict(_ZERO)))
    return estimate_cost(s, get_settings().model)


def snapshot(session_id: str | None = None) -> dict:
    cfg = get_settings()
    cfg_model = cfg.model
    with _lock:
        g = dict(_global)
        s = dict(_per_session.get(session_id, dict(_ZERO))) if session_id else None
    out: dict[str, Any] = {
        "model": cfg_model,
        "global": {**g, "cost_usd": estimate_cost(g, cfg_model)},
        "budget_usd": cfg.session_budget_usd,
    }
    if session_id:
        sess_cost = estimate_cost(s, cfg_model)  # type: ignore[arg-type]
        out["session_id"] = session_id
        out["session"] = {**s, "cost_usd": sess_cost}  # type: ignore[arg-type]
        out["over_budget"] = (
            cfg.session_budget_usd > 0 and sess_cost >= cfg.session_budget_usd
        )
    out["pricing_known"] = cfg_model in PRICING_PER_MTOK or _is_local(cfg_model)
    out["is_local"] = _is_local(cfg_model)
    return out


# ---- LangChain callback ---------------------------------------------------
class UsageCallback(BaseCallbackHandler):
    """Attach via `graph.invoke(state, config={"callbacks": [UsageCallback(sid)]})`
    to capture usage from EVERY LLM call inside the graph (planner, executor,
    critic, supervisor, sub-agents) without modifying individual nodes."""

    def __init__(self, session_id: str | None = None) -> None:
        super().__init__()
        self.session_id = session_id

    # Anthropic-style chat models call this with the LLMResult.
    def on_llm_end(self, response, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs):  # noqa: D401
        # 1) The most-portable path: messages with usage_metadata
        for gen_list in (response.generations or []):
            for g in gen_list:
                msg = getattr(g, "message", None)
                meta = getattr(msg, "usage_metadata", None) if msg is not None else None
                if meta:
                    add(dict(meta), self.session_id)
                    return  # one bundle per LLM call

        # 2) Fallback: provider's llm_output dict (some providers populate here)
        llm_output = getattr(response, "llm_output", None) or {}
        token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if token_usage:
            add({
                "input_tokens": token_usage.get("prompt_tokens") or token_usage.get("input_tokens"),
                "output_tokens": token_usage.get("completion_tokens") or token_usage.get("output_tokens"),
            }, self.session_id)
