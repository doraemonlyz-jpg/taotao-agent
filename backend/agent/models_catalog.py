"""Model discovery + runtime switching helpers.

Backs the /models and /model endpoints in app.py. Keeps two responsibilities:

  1. Probe what's *actually* available right now:
     - For Ollama → hit http://localhost:11434/api/tags
     - For hosted providers → check whether the matching API key env var is set
  2. When the user picks a new model, return a sensible `fast_model`
     pairing so the planner / critic / extractor still get a cheap tier.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

import httpx

from .config import get_settings

# ---------------------------------------------------------------------------
# Hosted catalogue — tiny curated list, kept in sync with usage.PRICING.
# Each entry is (model_id, friendly_label, "tier"):
#   tier="big"  → goes into AGENT_MODEL
#   tier="fast" → goes into AGENT_FAST_MODEL (used as auto-pair when picking big)
HOSTED: dict[str, list[tuple[str, str, str]]] = {
    "anthropic": [
        ("anthropic:claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", "big"),
        ("anthropic:claude-opus-4-5",            "Claude Opus 4.5",   "big"),
        ("anthropic:claude-haiku-4-5",           "Claude Haiku 4.5",  "fast"),
    ],
    "openai": [
        ("openai:gpt-4o",      "GPT-4o",       "big"),
        ("openai:gpt-4.1",     "GPT-4.1",      "big"),
        ("openai:gpt-4o-mini", "GPT-4o-mini",  "fast"),
    ],
    "google_genai": [
        ("google_genai:gemini-2.5-pro",   "Gemini 2.5 Pro",   "big"),
        ("google_genai:gemini-2.5-flash", "Gemini 2.5 Flash", "fast"),
    ],
}

PROVIDER_LABELS: dict[str, str] = {
    "ollama":       "Local (Ollama)",
    "anthropic":    "Anthropic",
    "openai":       "OpenAI",
    "google_genai": "Google Gemini",
}

PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic":    "ANTHROPIC_API_KEY",
    "openai":       "OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}

# Default pairings used when the user picks a `big` model without saying
# what to use as the `fast` tier. Falls back to the same model if none
# is available — every node still works, it just won't be cheaper.
HOSTED_FAST_DEFAULT: dict[str, str] = {
    "anthropic":    "anthropic:claude-haiku-4-5",
    "openai":       "openai:gpt-4o-mini",
    "google_genai": "google_genai:gemini-2.5-flash",
}


# ---------------------------------------------------------------------------
# Models that DO NOT support OpenAI-style function/tool calling.
# Picking one of these as the *big* model breaks any path that runs
# `bind_tools(...)` — i.e. the executor, every sub-agent, and ReAct loops.
# They can still be used as the *fast* model (router / critic / extractor
# rely on `with_structured_output`, which falls back to raw JSON mode).
#
# Match is by substring against the bare model name (after the "ollama:" prefix).
OLLAMA_NO_TOOL_PATTERNS: tuple[str, ...] = (
    # --- DeepSeek family: Ollama's templates don't expose tool calling for any
    #     of them, even though the models are technically tool-capable. Verified
    #     against ollama 0.23.x + langchain-ollama 1.1.0 (May 2026).
    "deepseek-r1",
    "deepseek-coder-v2",
    "deepseek-v2.5",
    "deepseek-v2",
    # --- other known no-tools families ---
    "gemma",                # gemma1/2/3 default templates omit tool calls
    "phi3", "phi4",         # phi3 mini / phi4 are unreliable for tools
    "qwen2-math", "qwen-math",
    "llama3.2:1b",          # too small — tool format breaks
    "llama3.2:3b",          # iffy
    "tinyllama", "tiny-",
)


def _ollama_supports_tools(model_id: str) -> bool:
    """model_id is like 'ollama:deepseek-r1:14b'. Returns False for the
    known-bad list above, True otherwise (best-effort guess)."""
    name = model_id.split(":", 1)[-1].lower() if model_id.startswith("ollama:") else ""
    return not any(p in name for p in OLLAMA_NO_TOOL_PATTERNS)


@dataclass
class ModelEntry:
    id: str
    label: str
    tier: str                       # "big" / "fast" / "any"
    size_gb: float | None = None    # only set for local models
    supports_tools: bool = True     # if False, can't be the executor / sub-agent model

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "tier": self.tier,
            "size_gb": self.size_gb,
            "supports_tools": self.supports_tools,
        }


@dataclass
class ProviderGroup:
    provider: str
    label: str
    available: bool
    reason: str | None
    models: list[ModelEntry]

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "label": self.label,
            "available": self.available,
            "reason": self.reason,
            "models": [m.to_dict() for m in self.models],
        }


# ---------------------------------------------------------------------------
def _ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://localhost:11434"


def discover_ollama() -> tuple[bool, str | None, list[ModelEntry]]:
    """Returns (available, reason_if_not, [models])."""
    url = _ollama_base_url().rstrip("/") + "/api/tags"
    try:
        r = httpx.get(url, timeout=2.0)
        r.raise_for_status()
        data = r.json()
    except httpx.ConnectError:
        return False, "ollama daemon not reachable — `brew services start ollama`", []
    except Exception as e:
        return False, f"ollama probe failed: {e!r}", []

    out: list[ModelEntry] = []
    for m in data.get("models") or []:
        name = m.get("name") or m.get("model")
        if not name:
            continue
        size_b = m.get("size") or 0
        mid = f"ollama:{name}"
        out.append(ModelEntry(
            id=mid,
            label=name,
            tier="any",   # local models can be either tier
            size_gb=round(size_b / 1_000_000_000, 1) if size_b else None,
            supports_tools=_ollama_supports_tools(mid),
        ))
    out.sort(key=lambda e: e.label)
    return True, None, out


def discover_hosted() -> Iterable[ProviderGroup]:
    for provider, models in HOSTED.items():
        env_key = PROVIDER_KEY_ENV.get(provider, "")
        env_val = os.getenv(env_key, "").strip() if env_key else ""
        # Treat dummy / placeholder values as "not configured"
        configured = bool(env_val) and not env_val.startswith(("sk-...",))
        yield ProviderGroup(
            provider=provider,
            label=PROVIDER_LABELS.get(provider, provider),
            available=configured,
            reason=None if configured else f"set {env_key} in backend/.env to enable",
            models=[ModelEntry(id=mid, label=label, tier=tier) for (mid, label, tier) in models],
        )


def list_all() -> dict:
    """Catalogue + currently-active picks. Shape matches what GET /models returns."""
    cfg = get_settings()
    groups: list[ProviderGroup] = []

    ok, reason, ollama_models = discover_ollama()
    groups.append(ProviderGroup(
        provider="ollama",
        label=PROVIDER_LABELS["ollama"],
        available=ok and bool(ollama_models),
        reason=reason if not ok else (None if ollama_models else "no models pulled — try `ollama pull qwen2.5:14b`"),
        models=ollama_models,
    ))
    groups.extend(discover_hosted())

    return {
        "current": cfg.model,
        "current_fast": cfg.fast_model,
        "groups": [g.to_dict() for g in groups],
    }


# ---------------------------------------------------------------------------
def model_supports_tools(model_id: str) -> bool:
    """Best-effort: True unless the model is on a known no-tools denylist.
    All hosted (anthropic / openai / gemini) catalogue entries support tools."""
    if model_id.startswith("ollama:"):
        return _ollama_supports_tools(model_id)
    return True


def auto_pair_fast(big_model: str) -> str:
    """Given a freshly-picked `big` model, return a sensible `fast` pairing."""
    if big_model.startswith("ollama:"):
        return big_model      # local — just reuse it; user can override later
    for prov, default_fast in HOSTED_FAST_DEFAULT.items():
        if big_model.startswith(prov + ":"):
            return default_fast
    return big_model
