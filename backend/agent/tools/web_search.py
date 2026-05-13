"""Web search tool with two backends + retry-with-backoff.

Backend selection (first available wins):

  1. Tavily   — if TAVILY_API_KEY is set. Best signal-to-noise, paid
                (1000 free / month). Recommended for production.
  2. DuckDuckGo via the `ddgs` package — keyless, free, but rate-limits
                aggressively (returns empty `No results` on backoff).

Both backends are wrapped in a small retry loop with jitter so a
transient 429 / connection reset doesn't surface as "No results" to
the LLM. Empty results from the *backend itself* still come through,
but with a hint to the LLM to rephrase rather than give up.
"""
from __future__ import annotations

import os
import random
import time

from langchain_core.tools import tool


def _format_hits(hits: list[dict]) -> str:
    """DDG/Tavily both return list[{title, url|href, body|content}]."""
    if not hits:
        return ""
    lines: list[str] = []
    for i, h in enumerate(hits, 1):
        title = h.get("title") or "(no title)"
        url = h.get("href") or h.get("url") or ""
        snip = (h.get("body") or h.get("content") or "").strip()
        lines.append(f"[{i}] {title}\n    {url}\n    {snip}")
    return "\n\n".join(lines)


def _search_tavily(query: str, max_results: int) -> list[dict]:
    """Tavily search · returns [] if not configured. Errors propagate."""
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    import httpx

    r = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
        },
        timeout=10.0,
    )
    r.raise_for_status()
    data = r.json()
    return [
        {"title": x.get("title"), "url": x.get("url"), "content": x.get("content")}
        for x in (data.get("results") or [])
    ]


def _search_ddg(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo search via the `ddgs` package. Empty list on backoff."""
    try:
        from ddgs import DDGS  # the renamed package
    except ImportError:  # pragma: no cover · fall back to legacy name
        from duckduckgo_search import DDGS  # type: ignore[import-not-found]
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the public web for recent information. Returns a numbered
    list of `{title, url, snippet}` blocks separated by blank lines.

    Use when:
      - the question depends on facts that may have changed after the
        model's training cutoff;
      - the user explicitly asks for sources / citations;
      - the topic is niche enough that recent web pages are likely to
        be more accurate than the model's parametric knowledge.

    Args:
        query: Natural-language search query. Be specific — DuckDuckGo
               drops short / single-word queries on rate-limit backoff.
        max_results: How many hits to return (1-10, default 5).
    """
    max_results = max(1, min(int(max_results), 10))

    # ---- 1. Try Tavily if configured (better signal, paid). -----
    if os.environ.get("TAVILY_API_KEY", "").strip():
        try:
            hits = _search_tavily(query, max_results)
            if hits:
                return _format_hits(hits)
        except Exception as e:
            # Don't fail the tool just because Tavily glitched; fall
            # through to DDG so the LLM still gets *something*.
            tavily_err = f"(tavily fallback: {e!r})"
        else:
            tavily_err = ""
    else:
        tavily_err = ""

    # ---- 2. DuckDuckGo via ddgs · retry on empty/error. ---------
    last_err: str = ""
    for attempt in range(3):
        try:
            hits = _search_ddg(query, max_results)
            if hits:
                return _format_hits(hits)
            last_err = "empty"
        except Exception as e:
            last_err = repr(e)
        # Backoff with jitter: 0.6s, 1.4s, 2.4s
        time.sleep(0.6 + attempt * 0.7 + random.random() * 0.4)

    # ---- 3. Honest empty-result message that nudges the LLM ----
    hint = (
        "No results returned (DuckDuckGo may be rate-limiting). "
        "Try rephrasing with more specific keywords, or split into a "
        "narrower query."
    )
    if tavily_err:
        hint = f"{hint} {tavily_err}"
    if last_err and last_err != "empty":
        hint = f"{hint} (last error: {last_err})"
    return hint
