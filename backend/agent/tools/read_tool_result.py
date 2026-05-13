"""`read_tool_result` · resume reading a tool result that was truncated.

Backstory:
  Big tool outputs (long file reads, big bash dumps, web pages) blow the
  context window if returned in full.  `safe_exec.py` truncates them to
  `tool_result_max_chars` and parks the FULL text in a process-local
  store, returning a marker like:

      …[truncated 12_300 chars · resume with read_tool_result(id='ab12cd34', offset=4000)]…

  This tool lets the model pull subsequent slices on demand, paginating
  through the full output without ever bloating the system message.

  Use cases:
    - bash output that listed 200 files and we only saw 60
    - read_file on a 500-line config we only got 90 lines of
    - web_search returned a wall of search snippets
"""
from __future__ import annotations

from langchain_core.tools import tool

from .safe_exec import get_stored_result


@tool
def read_tool_result(id: str, offset: int = 0, limit: int = 4000) -> str:
    """Resume reading a previous tool result that was truncated.

    Args:
        id: the short id printed in the truncation marker (10 hex chars).
        offset: how many chars to skip into the stored text. Use the
            `next_offset` value returned by the previous call to walk
            forward, or jump to a specific byte position.
        limit: how many chars to return this time. Default 4000 so you
            can chain calls without re-truncation.

    Returns:
        A small JSON-shaped block:
          {ok, tool, id, offset, next_offset, total, chunk}
        `next_offset` is null when you've reached the end.
    """
    res = get_stored_result(id, offset=offset, limit=limit)
    return str(res)
