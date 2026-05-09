"""Tools that let the LLM read/write the long-term Chroma memory itself."""
from __future__ import annotations

from langchain_core.tools import tool

from ..memory import get_memory


@tool
def remember(text: str, kind: str = "fact") -> str:
    """Persist a small fact / preference / learning to long-term memory so it
    survives across sessions. Use sparingly — only for things the user
    explicitly asks you to remember, or for hard-won knowledge.

    Args:
        text: The thing to remember (one sentence is best).
        kind: "fact" | "preference" | "episode" | "skill"
    """
    mem_id = get_memory().remember(text, kind=kind)
    return f"Remembered as {mem_id[:8]}"


@tool
def recall(query: str, k: int = 4) -> str:
    """Retrieve up to k pieces of long-term memory most relevant to `query`.
    Call this at the start of any conversation where prior context might help.

    Args:
        query: What you're trying to recall.
        k: How many memories to fetch.
    """
    hits = get_memory().recall(query, k=k)
    if not hits:
        return "(no relevant long-term memories)"
    return "\n".join(f"- {h}" for h in hits)
