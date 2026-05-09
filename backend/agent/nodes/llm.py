"""Helpers for getting a chat model.

Two tiers:
  • `get_llm()`        — the **powerful** model (executor, sub-agents).
  • `get_fast_llm()`   — the **cheap & fast** model used by router-style
                         nodes (planner, summarizer, critic, extractor).

Tier is chosen via Settings.model / Settings.fast_model. Both can point
at the same model if you don't care about cost optimisation.

Local models are first-class citizens. Three patterns work out-of-the-box:

  1. Ollama  (recommended on Apple Silicon)
       AGENT_MODEL=ollama:qwen2.5:14b
       OLLAMA_BASE_URL=http://localhost:11434      # optional, this is default

  2. LM Studio / vLLM / any OpenAI-compatible server
       AGENT_MODEL=openai:Qwen2.5-14B-Instruct
       OPENAI_BASE_URL=http://localhost:1234/v1
       OPENAI_API_KEY=lm-studio                    # any non-empty string

  3. Hosted (Anthropic / OpenAI / Gemini) — original behaviour, unchanged.
"""
from __future__ import annotations

import os
from functools import lru_cache

from langchain.chat_models import init_chat_model

from ..config import get_settings


def _provider_kwargs(name: str) -> dict:
    """Pick up env-supplied overrides so the same code path serves
    hosted APIs *and* local OpenAI-compatible servers / Ollama."""
    kwargs: dict = {}
    if name.startswith("openai:"):
        # LM Studio, vLLM, llama.cpp's openai server, OpenRouter, etc.
        base = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
        if base:
            kwargs["base_url"] = base
            # OpenAI client refuses to start without *some* key; local
            # servers ignore it. Provide a placeholder if the user didn't.
            if not os.getenv("OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = "local-no-key"
    elif name.startswith("ollama:"):
        base = os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST")
        if base:
            kwargs["base_url"] = base
    return kwargs


@lru_cache(maxsize=8)
def _build(name: str, temperature: float):
    return init_chat_model(name, temperature=temperature, **_provider_kwargs(name))


def get_llm(temperature: float = 0.2):
    """The big model — used wherever quality matters more than cost."""
    return _build(get_settings().model, temperature)


def get_fast_llm(temperature: float = 0.0):
    """The cheap model — used for routing/critique/summarisation/extraction."""
    cfg = get_settings()
    return _build(cfg.fast_model or cfg.model, temperature)


def reset_llm_cache() -> None:
    """Drop every cached chat-model instance so the next get_llm()/get_fast_llm()
    rebuilds against the current Settings. Called by `POST /model`."""
    _build.cache_clear()
