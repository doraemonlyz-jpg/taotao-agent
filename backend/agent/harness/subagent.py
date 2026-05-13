"""`dispatch_subagent` — the harness reframing of multi-agent.

# Why sub-agents become tools

In the graph version, sub-agents are first-class nodes:

    supervisor → researcher → supervisor → coder → supervisor → writer → critic

This forces:
  - A supervisor LLM call between every hand-off (cost)
  - A static set of specialists (3) baked into routing logic
  - Hard caps on visit counts (because the supervisor can loop)

In the harness, sub-agents are just a tool:

    main agent → dispatch_subagent(role="researcher", task="...")
              → returns a string
              → main agent decides what to do with it

What you GAIN:
  - The model decides when (and IF) to spawn a sub-agent · zero overhead
    for trivial queries
  - Easy to add a new specialist · register a new prompt + toolset, done
  - No orchestrator LLM call · the main agent IS the orchestrator
  - Sub-agent failures are just tool errors · main agent can retry / pivot

What you LOSE:
  - Sub-agent context isolation is now a CHOICE (we make it strict here:
    each dispatch starts with a clean message list)
  - You can't visualise a static graph
  - Concurrent sub-agents need explicit parallel tool calls (LLM-supported
    but you have to ask for it in the prompt)

# Implementation notes

Each sub-agent gets:
  - A focused system prompt (its job, no more)
  - A scoped tool subset (researcher only sees web_search; coder gets
    python_repl + file_ops, etc.)
  - Its own bounded loop (10 iterations max — sub-agents shouldn't think
    forever; if they need to, the main agent should plan smaller tasks)
  - NO access to long-term memory · they're stateless workers

The implementation is intentionally a simplified harness inside the
harness — a recursive structure that's nice to teach with.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool

from ..nodes.llm import get_llm
from ..observability import emit
from ..tools.file_ops import grep_in_files, list_files, read_file, write_file
from ..tools.python_repl import python_repl
from ..tools.safe_exec import safe_run_tool
from ..tools.web_search import web_search

# ---- per-role configuration ----------------------------------------------

_ROLES: dict[str, dict] = {
    "researcher": {
        "system": (
            "You are RESEARCHER · a focused web-search worker.  Use web_search "
            "aggressively · synthesise results · return a tight bullet summary "
            "with URLs.  Do NOT add commentary beyond the facts.  "
            "Stop after 1-3 searches; you don't need exhaustive coverage."
        ),
        "tools": [web_search],
        "max_steps": 6,
    },
    "coder": {
        "system": (
            "You are CODER · a focused programmer worker.  Write or run small "
            "Python via python_repl · save artefacts via write_file when "
            "useful.  Return both the code and the result.  "
            "Do not over-engineer; finish small tasks fast."
        ),
        "tools": [python_repl, read_file, write_file, list_files, grep_in_files],
        "max_steps": 10,
    },
    "writer": {
        "system": (
            "You are WRITER · take prior context and produce a polished final "
            "answer.  Be concise, structured, quotable.  Use file_ops to "
            "read source material if a path is provided.  Output answer only."
        ),
        "tools": [read_file, list_files, write_file, grep_in_files],
        "max_steps": 4,
    },
}

Role = Literal["researcher", "coder", "writer"]


def _run_inner_loop(role: str, task: str, session_id: str) -> str:
    """A miniature harness loop — same shape as the main one, scoped tools.

    Synchronous for simplicity; called from within the main async loop via
    `asyncio.to_thread` (see the @tool wrapper below).
    """
    cfg = _ROLES[role]
    msgs: list[AnyMessage] = [
        SystemMessage(content=cfg["system"]),
        HumanMessage(content=task),
    ]
    tools = cfg["tools"]
    tools_by_name = {t.name: t for t in tools}
    llm = get_llm(temperature=0.2).bind_tools(tools)

    for step in range(cfg["max_steps"]):
        resp: AIMessage = llm.invoke(msgs)
        msgs.append(resp)
        calls = getattr(resp, "tool_calls", None) or []
        if not calls:
            # natural finish
            return resp.content if isinstance(resp.content, str) else str(resp.content)

        emit("subagent", "tool_call",
             {"agent": role, "step": step, "calls": [c["name"] for c in calls]},
             session_id=session_id)

        for tc in calls:
            t = tools_by_name.get(tc["name"])
            if t is None:
                content = f"[error] unknown tool {tc['name']!r}"
            else:
                content = safe_run_tool(t, tc.get("args", {}))
            msgs.append(ToolMessage(content=content, name=tc["name"], tool_call_id=tc["id"]))

    # Hit the cap · ask the model for a best-effort summary so the caller
    # still gets something useful.
    msgs.append(HumanMessage(content="Hit max steps · summarise what you found in 3 lines and stop."))
    final: AIMessage = llm.invoke(msgs)
    return final.content if isinstance(final.content, str) else str(final.content)


# ---- the tool the main agent sees ----------------------------------------

@tool
def dispatch_subagent(role: str, task: str) -> str:
    """Spawn a focused worker sub-agent and return its result as text.

    Use this when a task naturally factors into a sub-task that benefits from
    its OWN context window and a SCOPED toolset — not for trivial work.

    PARALLELISM · IMPORTANT.
    The harness runs all your tool calls in a single turn CONCURRENTLY, so
    if you have 3 independent research questions, emit 3 dispatch_subagent
    calls in the SAME assistant turn · they will run side-by-side instead
    of sequentially.  Don't fan them out one per turn.

    Args:
        role: one of "researcher" (web search), "coder" (python + files),
              "writer" (polish a final answer from prior context)
        task: a precise, self-contained instruction.  The sub-agent does
              NOT see your conversation history — include needed context.

    Returns:
        The sub-agent's final answer string.

    Example tasks (good · parallel):
        # In one turn, emit:
        - dispatch_subagent(role="researcher", task="Find papers on PagedAttention; titles + URLs.")
        - dispatch_subagent(role="researcher", task="Find papers on FlashAttention 3; titles + URLs.")
        - dispatch_subagent(role="researcher", task="Find papers on speculative decoding; titles + URLs.")
        # → 3x faster than 3 separate turns.

    Example tasks (good · single):
        - role="coder",   task="Compute the SHA-256 of 'hello world' and return the hex."
        - role="writer",  task="Polish this draft into 3 bullet points: <draft text>"

    Example tasks (bad):
        - role="researcher", task="research stuff"   # too vague
        - role="coder", task="write me an OS"        # too big · break down first
    """
    if role not in _ROLES:
        return f"[error] unknown role {role!r} · valid: {list(_ROLES)}"
    if not task or len(task) < 8:
        return "[error] task must be a precise instruction (≥8 chars)"
    sid = ""  # tool decorator strips kwargs · session id is logged elsewhere
    try:
        # We're called from sync context inside the executor pool;
        # run synchronously.
        return _run_inner_loop(role, task, sid)
    except Exception as e:
        return f"[error] subagent {role!r} failed: {e}"
