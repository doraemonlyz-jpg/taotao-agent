"""Post-turn memory extractor — Mem0/LangMem-style auto-write.

Runs after the critic passes. Two key optimisations vs. the naive design:
  1. **Heuristic gate** — skips trivial turns (chitchat, short answers
     with no tool calls). Avoids ~70% of extractor LLM calls.
  2. **Async write** — the heavy LLM extraction + Chroma writes happen in
     a fire-and-forget thread, so the user gets their answer immediately.
"""
from __future__ import annotations

import threading

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from ..config import get_settings
from ..memory import get_memory
from ..memory.profile import get_profile
from ..memory.reflections import get_reflections
from ..observability import emit
from ..state import AgentState
from .llm import get_fast_llm

EXTRACT_PROMPT = """You audit a finished agent turn and extract anything
worth persisting.

Three kinds of artefacts:
  • facts        — durable, atomic statements about the USER.
                   Examples: "user lives in Tokyo", "user prefers Python".
                   Do NOT extract: trivia about the world, anything the
                   user did not personally tell us.
  • profile_updates — stable preferences worth pinning into the structured
                   profile (always-loaded). Use sparingly.
                   Keys MUST be snake_case. Values MUST be short strings.
                   Examples: {{"preferred_language": "zh"}},
                   {{"response_style": "concise"}}.
  • reflections  — lessons for the AGENT itself, learned from this turn.
                   Examples: "for math questions, the calculator tool is
                   faster than reasoning out loud".

Be conservative. Empty lists are correct most of the time."""


class Extracted(BaseModel):
    facts: list[str] = Field(default_factory=list)
    profile_updates: dict[str, str] = Field(default_factory=dict)
    reflections: list[str] = Field(default_factory=list)


_PERSIST_KEYWORDS = (
    "remember", "记住", "note that", "i prefer", "我喜欢", "我叫", "my name is",
    "from now on", "always", "never", "don't ", "do not ", "我是",
)


def _should_extract(user_text: str, final: str, critique: str, tool_calls: int) -> bool:
    """Heuristic gate — only fire the extractor when something useful might
    actually be in the turn."""
    u = user_text.lower()
    if len(user_text) < 8 or len(final) < 8:
        return False
    if critique:
        return True  # critic flagged something; lessons-learned are valuable
    if tool_calls > 0:
        return True  # if we used a tool, may be worth a workflow reflection
    if any(k in u for k in _PERSIST_KEYWORDS):
        return True  # explicit persistence cue
    return False


def _run_extraction(sid: str, user_text: str, final: str, critique: str) -> None:
    """The actual heavy work — runs off the request thread."""
    llm = get_fast_llm(temperature=0).with_structured_output(Extracted)
    prompt = ChatPromptTemplate.from_messages([
        ("system", EXTRACT_PROMPT),
        ("human",
         "USER MESSAGE:\n{u}\n\nAGENT FINAL ANSWER:\n{a}\n\n"
         "CRITIC NOTES (may be empty):\n{c}"),
    ])
    try:
        out: Extracted = (prompt | llm).invoke({"u": user_text, "a": final, "c": critique})
    except Exception as e:
        emit("extractor", "error", {"error": repr(e)}, session_id=sid)
        return

    if not (out.facts or out.profile_updates or out.reflections):
        return

    mem = get_memory()
    refl = get_reflections()
    prof = get_profile()
    written_facts: list[str] = []
    written_refl: list[str] = []

    for f in out.facts[:5]:
        if mem.remember_if_new(f, kind="fact", session_id=sid):
            written_facts.append(f)
    if out.profile_updates:
        prof.merge({str(k): v for k, v in out.profile_updates.items() if k})
    for r in out.reflections[:3]:
        if refl.add_if_new(r, source="extractor", session_id=sid):
            written_refl.append(r)

    emit("extractor", "memory_update", {
        "facts": written_facts,
        "facts_skipped_dup": len(out.facts) - len(written_facts),
        "profile_updates": out.profile_updates,
        "reflections": written_refl,
        "reflections_skipped_dup": len(out.reflections) - len(written_refl),
        "async": True,
    }, session_id=sid)


def extractor(state: AgentState) -> dict:
    """Sync portion: heuristic gate + spawn a background thread that does the
    LLM call + Chroma writes. Returns immediately so the user sees their
    answer with zero added latency."""
    cfg = get_settings()
    sid = state.get("session_id", "")
    if state.get("blocked"):
        return {}

    user_text = (state.get("user_input") or "").strip()
    final = (state.get("final_answer") or "").strip()
    critique = (state.get("critique") or "").strip()
    tool_calls = int(state.get("tool_calls_made", 0))

    if not _should_extract(user_text, final, critique, tool_calls):
        emit("extractor", "memory_update",
             {"facts": [], "profile_updates": {}, "reflections": [], "skipped": "gated"},
             session_id=sid)
        return {}

    t = threading.Thread(
        target=_run_extraction,
        args=(sid, user_text, final, critique),
        daemon=True,
        name=f"extractor-{sid[:8]}",
    )
    t.start()
    emit("extractor", "memory_update",
         {"facts": [], "profile_updates": {}, "reflections": [], "queued": True},
         session_id=sid)
    return {}
