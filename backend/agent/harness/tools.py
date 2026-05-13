"""Tool registry for the harness · existing 11 tools + 2 harness-specific.

Compared to `agent/tools/registry.py`, the harness deliberately:
  - Binds ALL tools every turn (no router · `select_tools`).
    Why: routing is a leaky abstraction · the LLM is better at picking
    which tool fits than a similarity-search heuristic, and modern models
    handle 15-20 tools fine.  If you grow past that, switch to
    progressive disclosure (see docs/tool-design.html ch.2).
  - Adds `dispatch_subagent` (parallel-friendly worker spawning).
  - Adds `final_answer` as an explicit terminator (see notes below) — but
    it's OPTIONAL · the loop also terminates on no-tool-call, like every
    other harness.

# Why expose final_answer as an explicit tool?

Two camps in practice:
  - "No-tool-call = done"     · simple, what Claude Code uses.
  - "Explicit final_answer()" · what some structured-output libs do,
                                makes ending a positive action the LLM
                                takes deliberately (better for evals).

We expose BOTH so you can A/B test for your model + use case.  The loop
treats `final_answer` as a stop condition AND treats no-tool-call as one
too · whichever the model prefers.
"""
from __future__ import annotations

from langchain_core.tools import tool

from ..tools.calculator import calculator
from ..tools.current_time import current_time
from ..tools.file_ops import grep_in_files, list_files, read_file, write_file
from ..tools.memory_tool import recall, remember
from ..tools.multi_agent_tool import multi_agent_run
from ..tools.profile_tool import read_profile, update_profile
from ..tools.propose_edit import apply_edit, propose_edit
from ..tools.python_repl import python_repl
from ..tools.read_tool_result import read_tool_result
from ..tools.skills_tool import load_skill
from ..tools.web_search import web_search
from ..mcp.client import load_external_tools as _load_mcp_client_tools

from .subagent import dispatch_subagent


@tool
def final_answer(answer: str) -> str:
    """Mark this `answer` as the final reply to the user and end the loop.

    Use this when you're done thinking and have a complete answer.
    The `answer` you pass becomes exactly what the user sees · so write it
    in their language, in their tone, with citations if you used web_search.

    You can ALSO end by simply not calling any tool · both work.  Use this
    explicit terminator when you want to be unambiguous (e.g. after a long
    chain of tool calls, or in evals where graders check for it).
    """
    # The loop intercepts this tool by NAME and terminates · the body is
    # never actually executed in production.  We return the answer here as
    # a courtesy in case some other path (testing, cli) does invoke it.
    return answer


# Order matters for prompt caching (Anthropic / OpenAI cache prefixes).
# Keep the most stable tools first; put dispatch_subagent + final_answer
# last because they're the most likely to be tweaked.
HARNESS_TOOLS = [
    # --- knowledge & retrieval -----------------------------------------
    web_search,
    read_file,
    list_files,
    grep_in_files,
    # --- compute -------------------------------------------------------
    calculator,
    current_time,
    python_repl,
    # --- memory --------------------------------------------------------
    recall,
    remember,
    read_profile,
    update_profile,
    load_skill,
    read_tool_result,
    # --- side effects --------------------------------------------------
    propose_edit,
    apply_edit,
    write_file,
    # --- harness control flow ------------------------------------------
    dispatch_subagent,
    multi_agent_run,
    final_answer,
]

# Append external MCP-client tools (named `<server>__<tool>`). Empty list
# when MCP_CLIENT_CONFIG is unset, so this is a no-op for default installs.
HARNESS_TOOLS.extend(_load_mcp_client_tools())


def tool_descriptions() -> list[dict]:
    """Same shape as the graph version · for the frontend's capabilities panel."""
    return [
        {"name": t.name, "description": (t.description or "").strip().split("\n")[0]}
        for t in HARNESS_TOOLS
    ]
