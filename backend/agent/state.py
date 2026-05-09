"""Agent state — flows through every node in the LangGraph."""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class TraceEvent(TypedDict, total=False):
    """One observability event — emitted by every node."""
    ts: float
    node: str
    kind: Literal[
        "perception", "guardrail", "plan", "tool_call", "tool_result",
        "subagent", "critique", "answer", "error", "token",
    ]
    payload: dict[str, Any]


class AgentState(TypedDict, total=False):
    # --- 5. Perception input ---
    user_input: str
    attachments: list[dict[str, Any]]

    # --- 1. LLM messages (short-term memory) ---
    messages: Annotated[list[AnyMessage], add_messages]

    # --- 4. Planning ---
    plan: list[str]
    current_step: int
    route: Literal["direct", "supervisor", "research", "code", "write"]

    # --- 6. Action / tool execution receipts ---
    tool_calls_made: int
    routed_tools: list[str]      # which tools the executor saw this turn

    # --- O. Orchestrator bookkeeping (single-turn) ---
    # Order of sub-agents already invoked this turn. Used by the supervisor
    # to (a) avoid repeating the same specialist in a loop and (b) force
    # termination after a hard cap. Reset by perception every turn.
    subagent_history: list[str]

    # --- Reflection ---
    critique: str
    critique_passed: bool
    revisions: int

    # --- Guardrails ---
    blocked: bool
    block_reason: str

    # --- Long-term memory hits (retrieved on demand) ---
    recalled_memories: list[str]
    recalled_reflections: list[str]

    # --- Rolling summary of compacted older messages ---
    summary: str

    # --- Final ---
    final_answer: str

    # --- Observability ---
    trace: Annotated[list[TraceEvent], add]
    session_id: str
