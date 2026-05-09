"""Optional component — Reflection / self-critique (Reflexion-style).

After the executor produces an answer, a critic model audits it. If the
critic finds an issue, the agent gets ONE chance to revise (bounded retries
prevent infinite loops). On the second pass we just ship whatever we have."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from ..config import get_settings
from ..memory.reflections import get_reflections
from ..observability import emit
from ..state import AgentState
from .llm import get_fast_llm

CRITIC_PROMPT = """You are a strict reviewer. Audit the assistant's draft answer
against the original user question. Check:
  - factual grounding (was a tool used when needed?)
  - completeness (did it answer all parts of the question?)
  - clarity and brevity

Return:
  - passed: true if the draft is good enough to ship
  - notes: short, actionable critique if not"""


class Critique(BaseModel):
    passed: bool = Field(description="True if the draft is fine as-is")
    notes: str = Field(description="Short critique. Empty if passed.")


def _last_ai_text(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            content = m.content
            if isinstance(content, str):
                return content
            # Anthropic streams content as a list of blocks; extract text
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if parts:
                    return "\n".join(parts)
            return str(content)
    return ""


def _tool_trail(messages) -> str:
    """Compact summary of tool calls + results so the critic doesn't
    hallucinate that 'no tool was used'."""
    lines: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                lines.append(f"  → {tc['name']}({tc.get('args', {})})")
    return "\n".join(lines) if lines else "  (no tool calls in this turn)"


def _should_skip_critic(user_q: str, draft: str, tool_calls_made: int) -> str | None:
    """Heuristic gates — skip the critic when the answer is obviously fine.

    Returns a reason string if we should skip, else None. Saves 1 LLM call
    per turn on chitchat / trivial answers, which is the most common case.
    """
    q = user_q.lower().strip()
    d = draft.strip()
    if len(d) < 30 and len(q) < 40:
        return "trivial-answer"
    if tool_calls_made == 0 and len(d) < 200 and "?" not in q:
        return "short-no-tools"
    if any(p in q for p in ("hi", "hello", "thanks", "你好", "谢谢", "嗨")) and len(q) < 25:
        return "greeting"
    return None


def critic(state: AgentState) -> dict:
    cfg = get_settings()
    sid = state.get("session_id", "")
    msgs = state.get("messages") or []
    draft = _last_ai_text(msgs)
    user_q = state.get("user_input", "")
    tool_calls_made = state.get("tool_calls_made", 0)

    if not cfg.critic_enabled or not draft:
        return {"final_answer": draft, "critique_passed": True}

    revisions = state.get("revisions", 0)
    # second pass — ship without re-critiquing
    if revisions >= 1:
        emit("critic", "critique",
             {"revisions": revisions, "passed": True, "notes": "(second pass — shipping)"},
             session_id=sid)
        return {"final_answer": draft, "critique_passed": True}

    # heuristic gate — most turns don't need a critic round-trip
    skip_reason = _should_skip_critic(user_q, draft, tool_calls_made)
    if skip_reason:
        emit("critic", "critique",
             {"passed": True, "notes": "", "revisions": 0, "skipped": skip_reason},
             session_id=sid)
        return {"final_answer": draft, "critique_passed": True}

    llm = get_fast_llm(temperature=0).with_structured_output(Critique)
    prompt = ChatPromptTemplate.from_messages([
        ("system", CRITIC_PROMPT),
        ("human",
         "USER QUESTION:\n{q}\n\nTOOL CALLS THE AGENT MADE:\n{trail}\n\n"
         "DRAFT ANSWER:\n{a}"),
    ])
    try:
        verdict: Critique = (prompt | llm).invoke({
            "q": user_q,
            "trail": _tool_trail(msgs),
            "a": draft,
        })
    except Exception as e:
        emit("critic", "error", {"error": repr(e)}, session_id=sid)
        return {"final_answer": draft, "critique_passed": True}

    emit("critic", "critique",
         {"passed": verdict.passed, "notes": verdict.notes, "revisions": revisions},
         session_id=sid)

    if verdict.passed:
        return {"final_answer": draft, "critique": verdict.notes, "critique_passed": True}

    # Persist the critique as a reflection — next time a similar question
    # arrives, perception will recall it and the agent skips the mistake.
    # Use add_if_new to avoid spamming the store with near-duplicates.
    try:
        get_reflections().add_if_new(
            f"On a question like '{user_q[:120]}': {verdict.notes}",
            source="critic",
            session_id=sid,
        )
    except Exception:
        pass

    # ask the executor to try again, with the critique appended.
    # NB: Anthropic refuses non-consecutive system messages, so we use
    # a HumanMessage to carry the critic's note back into the loop.
    revise_msg = HumanMessage(
        content=f"[Reviewer feedback]\n{verdict.notes}\n\n"
                f"Please revise your previous answer accordingly and reply "
                f"with the improved final answer only."
    )
    return {
        "messages": [revise_msg],
        "revisions": revisions + 1,
        "critique": verdict.notes,
        "critique_passed": False,
    }


def route_after_critic(state: AgentState):
    if state.get("critique_passed", True):
        return "output_guardrail"
    return "executor"
