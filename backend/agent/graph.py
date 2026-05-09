"""The full StateGraph wiring every component in your spec.

Visual:

    user input
        │
        ▼
   ┌────────────┐
   │ perception │  (5) parse + recall long-term memory
   └─────┬──────┘
         ▼
   ┌─────────────────┐
   │ input_guardrail │  (G) refuse prompt-injection
   └─────┬───────────┘
         ▼
   ┌──────────┐
   │ planner  │  (4) decide: direct ReAct or supervisor multi-agent
   └─┬──────┬─┘
     │      └─────────────────┐
     ▼ direct                 ▼ supervisor
 ┌──────────┐            ┌────────────┐
 │ executor │◄───┐       │ supervisor │──► researcher / coder / writer
 └────┬─────┘    │       └────────────┘
      │ tools?   │              │ done
      ▼          │              ▼
 ┌──────────┐    │         ┌─────────┐
 │  tools   │────┘         │ critic  │  (R) reflexion
 └──────────┘              └────┬────┘
                                ▼
                       ┌──────────────────┐
                       │ output_guardrail │  (G) PII redaction
                       └────────┬─────────┘
                                ▼
                              answer
"""
from __future__ import annotations

import aiosqlite
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from .config import get_settings

from .nodes import (
    coder_subagent,
    critic,
    executor,
    extractor,
    get_llm,
    input_guardrail,
    output_guardrail,
    perception,
    planner,
    researcher_subagent,
    route_after_executor,
    route_after_planner,
    route_supervisor,
    summarizer,
    supervisor,
    tool_node,
    writer_subagent,
)
from .nodes.critic import route_after_critic
from .state import AgentState


def build_graph():
    g = StateGraph(AgentState)

    # --- nodes -----------------------------------------------------------
    g.add_node("summarizer", summarizer)
    g.add_node("perception", perception)
    g.add_node("input_guardrail", input_guardrail)
    g.add_node("planner", planner)
    g.add_node("executor", executor)
    g.add_node("tools", tool_node)
    g.add_node("supervisor", supervisor)
    g.add_node("researcher", researcher_subagent)
    g.add_node("coder", coder_subagent)
    g.add_node("writer", writer_subagent)
    g.add_node("critic", critic)
    g.add_node("extractor", extractor)
    g.add_node("output_guardrail", output_guardrail)

    # --- edges -----------------------------------------------------------
    g.add_edge(START, "summarizer")
    g.add_edge("summarizer", "perception")
    g.add_edge("perception", "input_guardrail")
    g.add_edge("input_guardrail", "planner")

    g.add_conditional_edges(
        "planner",
        route_after_planner,
        {"executor": "executor", "supervisor": "supervisor", "output_guardrail": "output_guardrail"},
    )

    # ReAct loop
    g.add_conditional_edges(
        "executor",
        route_after_executor,
        {"tools": "tools", "critic": "critic", "output_guardrail": "output_guardrail"},
    )
    g.add_edge("tools", "executor")

    # Supervisor sub-graph
    g.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {"researcher": "researcher", "coder": "coder", "writer": "writer", "critic": "critic"},
    )
    g.add_edge("researcher", "supervisor")
    g.add_edge("coder", "supervisor")
    g.add_edge("writer", "critic")

    # Reflection — if critic asks for revision, bounce back to executor;
    # otherwise pass to extractor (Mem0-style auto memory writes), which
    # then hands off to the output guardrail.
    g.add_conditional_edges(
        "critic",
        route_after_critic,
        {"executor": "executor", "output_guardrail": "extractor"},
    )

    g.add_edge("extractor", "output_guardrail")
    g.add_edge("output_guardrail", END)
    return g  # caller compiles with the saver of their choice


# Two compiled graph singletons — one with the AsyncSqliteSaver for
# the streaming `/chat` endpoint, one with the sync SqliteSaver for any
# blocking call paths (currently unused, kept for parity).
# Both share the same SQLite file so checkpoints carry over either way.
_async_compiled = None
_sync_compiled = None


def get_graph():
    """Streaming-friendly graph (AsyncSqliteSaver). Survives backend restarts."""
    global _async_compiled
    if _async_compiled is None:
        cfg = get_settings()
        async_conn = aiosqlite.connect(str(cfg.checkpoint_db))
        saver = AsyncSqliteSaver(async_conn)
        _async_compiled = build_graph().compile(checkpointer=saver)
    return _async_compiled


def get_sync_graph():
    """Synchronous equivalent for any non-streaming callers."""
    global _sync_compiled
    if _sync_compiled is None:
        cfg = get_settings()
        conn = sqlite3.connect(str(cfg.checkpoint_db), check_same_thread=False)
        saver = SqliteSaver(conn)
        _sync_compiled = build_graph().compile(checkpointer=saver)
    return _sync_compiled


__all__ = ["build_graph", "get_graph", "get_sync_graph"]
