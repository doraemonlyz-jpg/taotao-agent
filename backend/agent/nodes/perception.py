"""Component 5 — Perception.

Builds the working state for the planner/executor:
  • a single SystemMessage carrying the static guidance + skills index
    (marked with Anthropic `cache_control` so subsequent turns hit the
    prompt cache and pay 1/10 the input cost)
  • a HumanMessage carrying per-turn dynamic context: profile snapshot,
    recalled facts, recalled reflections, and the user's actual input.

Why split into two messages?
  Cache hits require BYTE-IDENTICAL prefix. The static block almost never
  changes; the dynamic block changes every turn. Putting them together
  would invalidate the cache on every request."""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import get_settings
from ..memory import get_memory
from ..memory.profile import get_profile
from ..memory.reflections import get_reflections
from ..memory.skills import skills_index_block
from ..observability import emit
from ..state import AgentState

STATIC_SYSTEM_PROMPT = """You are 桃桃 Agent (Taotao Agent), a capable, careful AI assistant.

Abilities you have:
- think step by step before acting
- call tools when external information or computation is needed
- read and write four kinds of memory:
    * messages   — short-term chat history (auto-summarised when long)
    * facts      — vector long-term store; use `remember` / `recall`
    * profile    — structured stable user data; use `update_profile` / `read_profile`
    * skills     — procedural recipes loaded from disk; use `load_skill`
- delegate to specialist sub-agents (research, code, writing) for complex tasks
- critique your own answer and revise it once before responding

Operating rules:
1. Skills first.   At the start of any non-trivial task, scan the skills
   index below. If one applies, call `load_skill(name)` BEFORE planning.
2. Profile first.  Trust the profile block — it's the user's stable
   preferences. If something there is wrong, ask the user.
3. No fabrication. NEVER invent quantitative facts — use the calculator
   or web_search.
4. Keep answers tight. Prefer 3 short paragraphs over 10 bullets.
5. Persist sparingly. The post-turn extractor handles routine memory
   writes for you. Only call `remember` / `update_profile` yourself when
   the user explicitly asks ("remember that I prefer X")."""


def _build_static_system(prompt_caching: bool):
    """Build the SystemMessage. Static = identical across turns,
    so we attach Anthropic cache_control to make it cacheable."""
    skills_block = skills_index_block()
    text = STATIC_SYSTEM_PROMPT
    if skills_block:
        text += "\n\n---\n" + skills_block

    if prompt_caching:
        return SystemMessage(content=[
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
        ])
    return SystemMessage(content=text)


def perception(state: AgentState) -> dict:
    cfg = get_settings()
    sid = state.get("session_id", "")
    user_text = (state.get("user_input") or "").strip()
    attachments = state.get("attachments", []) or []

    facts: list[str] = []
    reflections: list[str] = []
    used_hyde = False
    if user_text:
        # HyDE for short / vague queries; cheap cosine for everything else
        use_hyde = len(user_text) < 25 and "?" not in user_text and "？" not in user_text
        try:
            mem = get_memory()
            facts = mem.recall_hyde(user_text, k=3) if use_hyde else mem.recall(user_text, k=3)
            used_hyde = use_hyde
        except Exception:
            facts = []
        try:
            reflections = get_reflections().recall(user_text, k=2)
        except Exception:
            reflections = []

    profile_block = get_profile().to_prompt_block()

    emit(
        "perception", "perception",
        {
            # First 240 chars of the user's text so /chat/replay can find
            # this turn later by session_id without storing the full body
            # twice. Keep ASCII-safe for the JSONL line.
            "text": user_text[:240],
            "chars": len(user_text),
            "n_attachments": len(attachments),
            "facts_recalled": len(facts),
            "reflections_recalled": len(reflections),
            "profile_keys": list(get_profile().all().keys()),
            "hyde": used_hyde,
        },
        session_id=sid,
    )

    msgs = list(state.get("messages") or [])

    # Inject the static (cacheable) system message exactly once per thread.
    if not any(isinstance(m, SystemMessage) for m in msgs):
        prompt_caching = cfg.model.startswith("anthropic:")
        msgs.append(_build_static_system(prompt_caching))

    # Per-turn dynamic context goes into a HumanMessage so the static
    # SystemMessage above stays byte-identical (cacheable).
    dynamic_parts: list[str] = []
    if profile_block:
        dynamic_parts.append(profile_block)
    if facts:
        dynamic_parts.append("[Recalled facts]\n" + "\n".join(f"- {f}" for f in facts))
    if reflections:
        dynamic_parts.append("[Recalled reflections]\n" + "\n".join(f"- {r}" for r in reflections))
    dynamic_parts.append("[Current message]\n" + user_text)

    msgs.append(HumanMessage(content="\n\n".join(dynamic_parts)))

    return {
        "messages": msgs,
        "recalled_memories": facts,
        "recalled_reflections": reflections,
        "tool_calls_made": 0,
        "revisions": 0,
        "routed_tools": [],
        "subagent_history": [],
    }
