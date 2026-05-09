"""Component 6 — Action execution (ReAct loop).

`executor` is the LLM-with-tools node. `tool_node` actually runs the tools.
Together they form the standard ReAct cycle:
    executor → (tool calls?) → tool_node → executor → ... → critic
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from ..config import get_settings
from ..observability import emit
from ..state import AgentState
from ..tools import all_tools, select_tools
from ..tools.safe_exec import safe_run_tool
from .llm import get_llm

# Tools whose results are non-deterministic / stateful — never cache them.
_NON_CACHEABLE = {"python_repl", "write_file", "remember", "update_profile"}


def _to_text(content) -> str:
    """Coerce an AIMessage.content (str | list of blocks) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    return str(content) if content is not None else ""


_TOOLS_BY_NAME = {t.name: t for t in all_tools}


def tool_node(state: AgentState) -> dict:
    """Custom ToolNode replacement: per-call timeout + LRU cache +
    result truncation, plus a `tool_result` trace event for observability.

    Dispatches against the FULL tool registry (the router only narrowed
    what the LLM saw, not what we're capable of running)."""
    sid = state.get("session_id", "")
    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    if not isinstance(last, AIMessage) or not getattr(last, "tool_calls", None):
        return {}

    out_msgs: list[ToolMessage] = []
    for tc in last.tool_calls:
        name = tc["name"]
        args = tc.get("args", {})
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            content = f"[error] unknown tool {name!r}"
        else:
            content = safe_run_tool(tool, args, cacheable=name not in _NON_CACHEABLE)

        emit("tools", "tool_result",
             {"name": name, "chars": len(content), "preview": content[:240]},
             session_id=sid)
        out_msgs.append(ToolMessage(content=content, name=name, tool_call_id=tc["id"]))

    return {"messages": out_msgs}


def executor(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    if state.get("blocked"):
        return {}

    msgs = state.get("messages") or []

    # Route to a relevant subset of tools (top-K by Settings).
    # First call we route on the user's question; subsequent ReAct ticks
    # we keep the same set so the model isn't surprised by a vanishing tool.
    routed = state.get("routed_tools")
    if not routed:
        chosen = select_tools(state.get("user_input", ""))
        routed = [t.name for t in chosen]
        if len(routed) < len(all_tools):
            emit("executor", "tool_call",
                 {"calls": [], "routed_tools": routed, "kind": "tool_routing"},
                 session_id=sid)
    else:
        chosen = [t for t in all_tools if t.name in set(routed)]

    response: AIMessage = get_llm(temperature=0.2).bind_tools(chosen).invoke(msgs)

    tool_calls = getattr(response, "tool_calls", []) or []
    if tool_calls:
        emit("executor", "tool_call",
             {"calls": [{"name": tc["name"], "args": tc.get("args", {})} for tc in tool_calls]},
             session_id=sid)
    else:
        text = _to_text(response.content)
        emit("executor", "answer", {"text": text[:600]}, session_id=sid)

    return {
        "messages": [response],
        "tool_calls_made": state.get("tool_calls_made", 0) + len(tool_calls),
        "routed_tools": routed,
    }


def route_after_executor(state: AgentState):
    """If the last AIMessage produced tool calls → run them. Else → critic."""
    if state.get("blocked"):
        return "output_guardrail"

    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        if state.get("tool_calls_made", 0) < get_settings().max_loop_iters:
            return "tools"
    return "critic"
