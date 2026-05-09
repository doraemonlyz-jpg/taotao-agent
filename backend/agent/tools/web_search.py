"""Web search via DuckDuckGo — no API key needed."""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the public web for recent information. Returns a list of
    {title, url, snippet} as text.

    Use this when the question depends on facts that may have changed
    after the model's training cutoff, or when the user asks for citations.

    Args:
        query: Natural-language search query.
        max_results: How many hits to return (default 5).
    """
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"Web search failed: {e!r}"
    if not hits:
        return "No results."
    lines = []
    for i, h in enumerate(hits, 1):
        title = h.get("title", "(no title)")
        url = h.get("href") or h.get("url", "")
        snip = h.get("body", "").strip()
        lines.append(f"[{i}] {title}\n    {url}\n    {snip}")
    return "\n\n".join(lines)
