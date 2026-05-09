"""Short-term memory compaction.

Runs at the START of every turn. If the message log is longer than
SUMMARIZE_THRESHOLD, distil the older portion into a single SystemMessage
and delete the originals via RemoveMessage. Keeps the recent KEEP_RECENT
turns verbatim so the agent doesn't lose immediate context."""
from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.graph.message import RemoveMessage

from ..config import get_settings
from ..observability import emit
from ..state import AgentState
from .llm import get_fast_llm

SUMMARIZE_THRESHOLD = 24
KEEP_RECENT = 8


def _format(msgs: list[AnyMessage]) -> str:
    out: list[str] = []
    for m in msgs:
        role = m.__class__.__name__.replace("Message", "").lower()
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            calls = ", ".join(tc["name"] for tc in m.tool_calls)
            out.append(f"[{role}] (tool_calls: {calls})")
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        out.append(f"[{role}] {content[:400]}")
    return "\n".join(out)


def summarizer(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    msgs = list(state.get("messages") or [])
    if len(msgs) <= SUMMARIZE_THRESHOLD:
        return {}

    older = msgs[:-KEEP_RECENT]
    older_with_id = [m for m in older if getattr(m, "id", None)]
    if not older_with_id:
        return {}

    prior_summary = state.get("summary", "")
    prompt = (
        ("Earlier summary (extend this, don't repeat):\n" + prior_summary + "\n\n"
         if prior_summary else "")
        + "Conversation to summarise:\n"
        + _format(older)
        + "\n\nReturn 4-6 bullet points capturing: facts established, "
          "decisions made, tools used (and outcome), unresolved threads. "
          "No fluff."
    )

    try:
        resp = get_fast_llm(temperature=0).invoke([HumanMessage(content=prompt)])
        summary = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        emit("summarizer", "error", {"error": repr(e)}, session_id=sid)
        return {}

    deletions = [RemoveMessage(id=m.id) for m in older_with_id]
    summary_msg = SystemMessage(content=f"[Conversation so far — summary]\n{summary}")

    emit("summarizer", "perception",
         {"compacted": len(older_with_id), "kept": KEEP_RECENT, "summary_chars": len(summary)},
         session_id=sid)

    return {
        "messages": deletions + [summary_msg],
        "summary": summary,
    }
