"""Short-term memory = the LangGraph MessagesState window, with optional
auto-compaction once it grows past a threshold."""
from __future__ import annotations

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage

MAX_MESSAGES = 30
KEEP_RECENT = 10


def compact_messages(messages: list[AnyMessage], summarizer=None) -> list[AnyMessage]:
    """If conversation grows too long, summarise the older turns and
    keep only the recent N + summary."""
    if len(messages) <= MAX_MESSAGES:
        return messages
    older = messages[:-KEEP_RECENT]
    recent = messages[-KEEP_RECENT:]
    if summarizer is None:
        # cheap fallback: drop older without summarising
        return recent
    summary_text = summarizer(older)
    summary_msg = SystemMessage(
        content=f"[Conversation so far, summarised]\n{summary_text}"
    )
    return [summary_msg, *recent]
