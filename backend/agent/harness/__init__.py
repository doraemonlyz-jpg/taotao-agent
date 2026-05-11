"""Harness-style agent — the parallel implementation to the graph in
`agent/nodes/`.  Same tools, same memory, same observability — radically
different control flow:

    graph version:     13 fixed nodes + conditional edges; framework decides flow
    harness version:   1 while-loop; LLM decides flow via tool_calls

See docs/harness.html for the full design rationale and per-line walkthrough.
"""
from __future__ import annotations

from .loop import run_harness, Harness
from .persistence import HarnessSessionStore

__all__ = ["run_harness", "Harness", "HarnessSessionStore"]
