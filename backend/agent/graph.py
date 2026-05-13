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

import logging
import os
import sqlite3

import aiosqlite
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from .config import get_settings

log = logging.getLogger("agent.graph")


def _postgres_dsn() -> str | None:
    """Return the Postgres DSN if configured, else None.

    Production deploys set `DATABASE_URL=postgresql://...` and the graph
    automatically swaps SqliteSaver → PostgresSaver. Connection pooling
    is handled by langgraph-checkpoint-postgres which uses psycopg.

    Schema migration runs on first connect (idempotent).  No Alembic
    needed · langgraph-checkpoint-postgres ships its own DDL.
    """
    v = (os.environ.get("DATABASE_URL") or "").strip()
    if not v:
        return None
    if not v.startswith(("postgres://", "postgresql://")):
        return None
    return v

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


def _build_async_saver():
    """Pick AsyncSqliteSaver (default) or AsyncPostgresSaver (DATABASE_URL set).

    PostgresSaver is the production choice: durable across backend
    restarts, supports concurrent processes (uvicorn workers > 1),
    handles backups via standard pg_dump.

    SqliteSaver is fine for local dev and single-process demos · it
    locks the DB file so multiple uvicorn workers will deadlock on it.
    """
    cfg = get_settings()
    dsn = _postgres_dsn()
    if dsn:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            log.info("graph checkpointer: AsyncPostgresSaver (DATABASE_URL set)")
            # The Postgres saver wants its setup() called once · safe to
            # call repeatedly (idempotent CREATE TABLE IF NOT EXISTS).
            saver_cm = AsyncPostgresSaver.from_conn_string(dsn)
            # `from_conn_string` returns an async context manager that yields the
            # saver. We enter it and never exit · matches the SqliteSaver
            # lifetime (singleton per process).  Exit happens at process exit.
            saver = saver_cm.__aenter__  # store the awaitable factory
            return ("postgres", saver_cm)
        except ImportError as e:
            log.error(
                "DATABASE_URL set but langgraph-checkpoint-postgres not installed: %s · "
                "falling back to sqlite. Install with: uv add langgraph-checkpoint-postgres",
                e,
            )
        except Exception as e:  # pragma: no cover · degrade gracefully
            log.warning("Postgres saver init failed (%s) · falling back to sqlite", e)

    log.info("graph checkpointer: AsyncSqliteSaver · %s", cfg.checkpoint_db)
    async_conn = aiosqlite.connect(str(cfg.checkpoint_db))
    return ("sqlite", AsyncSqliteSaver(async_conn))


def _build_sync_saver():
    """Sync equivalent · same Postgres-when-set logic."""
    cfg = get_settings()
    dsn = _postgres_dsn()
    if dsn:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            log.info("sync graph checkpointer: PostgresSaver")
            saver_cm = PostgresSaver.from_conn_string(dsn)
            return ("postgres", saver_cm)
        except ImportError as e:
            log.error("langgraph-checkpoint-postgres not installed: %s", e)
        except Exception as e:  # pragma: no cover
            log.warning("sync Postgres saver init failed: %s", e)

    log.info("sync graph checkpointer: SqliteSaver · %s", cfg.checkpoint_db)
    conn = sqlite3.connect(str(cfg.checkpoint_db), check_same_thread=False)
    return ("sqlite", SqliteSaver(conn))


def get_graph():
    """Streaming-friendly graph · async checkpointer (Postgres or sqlite)."""
    global _async_compiled
    if _async_compiled is None:
        backend, saver = _build_async_saver()
        if backend == "postgres":
            # Postgres saver returns a context manager · enter it lazily
            # via a small adapter so callers can keep using the existing
            # async-graph API.  The CM stays open for the process lifetime.
            import asyncio

            real_saver = asyncio.get_event_loop().run_until_complete(saver.__aenter__())
            try:
                asyncio.get_event_loop().run_until_complete(real_saver.setup())
            except Exception as e:  # pragma: no cover · idempotent setup
                log.debug("Postgres saver setup() noop: %s", e)
            _async_compiled = build_graph().compile(checkpointer=real_saver)
        else:
            _async_compiled = build_graph().compile(checkpointer=saver)
    return _async_compiled


def get_sync_graph():
    """Synchronous equivalent for any non-streaming callers."""
    global _sync_compiled
    if _sync_compiled is None:
        backend, saver = _build_sync_saver()
        if backend == "postgres":
            real_saver = saver.__enter__()
            try:
                real_saver.setup()
            except Exception as e:  # pragma: no cover
                log.debug("sync Postgres setup noop: %s", e)
            _sync_compiled = build_graph().compile(checkpointer=real_saver)
        else:
            _sync_compiled = build_graph().compile(checkpointer=saver)
    return _sync_compiled


__all__ = ["build_graph", "get_graph", "get_sync_graph"]
