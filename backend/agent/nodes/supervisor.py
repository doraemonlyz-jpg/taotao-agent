"""Optional component — Orchestrator (supervisor + sub-agents).

Pattern: supervisor reads the plan + last subagent output, decides which
specialist runs next ("research" | "code" | "write" | "done"). Each
sub-agent is a small ReAct loop with a scoped toolset and a focused
system prompt.

Kept deliberately compact for the demo — production multi-agent systems
add per-agent state, message-pass schemas, retries, and budgets."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from ..observability import emit
from ..state import AgentState
from ..tools import all_tools
from ..tools.file_ops import list_files, read_file, write_file
from ..tools.python_repl import python_repl
from ..tools.web_search import web_search
from .llm import get_fast_llm, get_llm

# --- Tool subsets per specialist ----------------------------------------
RESEARCH_TOOLS = [web_search]
CODE_TOOLS = [python_repl, read_file, write_file, list_files]
WRITE_TOOLS = [read_file, write_file, list_files]

# --- Sub-agent ReAct factories ------------------------------------------
def _make_subagent(prompt: str, tools):
    return create_react_agent(get_llm(temperature=0.2), tools=tools, prompt=prompt)


_RESEARCHER_PROMPT = """You are the RESEARCHER. Find facts on the public web.
Use web_search aggressively, then return a tight bullet summary with URLs."""
_CODER_PROMPT = """You are the CODER. Write or run small Python programs to
compute or verify something. Use python_repl. Save artefacts via write_file
when useful. Return both the code and the result."""
_WRITER_PROMPT = """You are the WRITER. Take the prior context and produce a
polished final answer. Be concise, structured, and quotable."""


def _label(content) -> str:
    if isinstance(content, str):
        return content
    return str(content)


def _append_history(state: AgentState, name: str) -> list[str]:
    """Return a NEW history list with `name` appended. We replace the field
    each time (no reducer) so perception's reset still works."""
    return list(state.get("subagent_history") or []) + [name]


def researcher_subagent(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    plan = "\n".join(f"- {s}" for s in state.get("plan", []))
    agent = _make_subagent(_RESEARCHER_PROMPT, RESEARCH_TOOLS)
    out = agent.invoke({"messages": [HumanMessage(
        content=f"User question: {state.get('user_input','')}\n\nPlan:\n{plan}"
    )]})
    last = out["messages"][-1]
    text = _label(last.content)
    emit("researcher", "subagent",
         {"agent": "researcher", "out": text[:600]}, session_id=sid)
    return {
        "messages": [AIMessage(content=f"[Research]\n{text}")],
        "subagent_history": _append_history(state, "researcher"),
    }


def coder_subagent(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    plan = "\n".join(f"- {s}" for s in state.get("plan", []))
    agent = _make_subagent(_CODER_PROMPT, CODE_TOOLS)
    out = agent.invoke({"messages": [HumanMessage(
        content=f"User question: {state.get('user_input','')}\n\nPlan:\n{plan}"
    )]})
    last = out["messages"][-1]
    text = _label(last.content)
    emit("coder", "subagent",
         {"agent": "coder", "out": text[:600]}, session_id=sid)
    return {
        "messages": [AIMessage(content=f"[Code]\n{text}")],
        "subagent_history": _append_history(state, "coder"),
    }


def writer_subagent(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    msgs = state.get("messages") or []
    context_blob = "\n\n".join(
        m.content if isinstance(m.content, str) else str(m.content)
        for m in msgs[-6:] if isinstance(m, AIMessage)
    )
    agent = _make_subagent(_WRITER_PROMPT, WRITE_TOOLS)
    out = agent.invoke({"messages": [HumanMessage(
        content=f"User question: {state.get('user_input','')}\n\n"
                f"Context from prior sub-agents:\n{context_blob or '(none)'}\n\n"
                f"Produce the final answer."
    )]})
    last = out["messages"][-1]
    text = _label(last.content)
    emit("writer", "subagent",
         {"agent": "writer", "out": text[:600]}, session_id=sid)
    return {
        "messages": [AIMessage(content=text)],
        "subagent_history": _append_history(state, "writer"),
    }


# --- Supervisor router ---------------------------------------------------
SUPERVISOR_PROMPT = """You are a supervisor coordinating three specialist agents:
  - "research": find external facts via web search
  - "code"    : run python / read & write files
  - "write"   : produce the final polished answer (must run before "done")
  - "done"    : end the multi-agent loop (only AFTER write has produced an answer)

You will see what has already happened (specialist visit counts).
Termination rules you MUST follow:
  • Never pick the same specialist more than 2 times.
  • If "writer" has already run, the only valid next step is "done".
  • If you've used >= 3 specialist hops without picking "write", pick "write" now.
  • Prefer "write" as soon as you have enough context — over-researching is wasteful.
"""


class Decision(BaseModel):
    next: str = Field(description="research | code | write | done")
    reason: str


# Hard caps — used to short-circuit the LLM if it stalls or refuses to
# advance. Tuned for the demo: at most 2 visits to any specialist, at
# most 5 supervisor hops before forcing the writer.
MAX_PER_SPECIALIST = 2
MAX_TOTAL_HOPS = 5


def _force_route(history: list[str]) -> str | None:
    """Returns a forced next step (bypassing the LLM) when state alone tells
    us where to go — or None to let the LLM decide."""
    if "writer" in history:
        # Writer has already produced something — always exit the loop.
        return "done"
    n_research = history.count("researcher")
    n_code     = history.count("coder")
    if len(history) >= MAX_TOTAL_HOPS:
        return "write"
    # If both specialists already capped out and writer hasn't run, write now.
    if n_research >= MAX_PER_SPECIALIST and n_code >= MAX_PER_SPECIALIST:
        return "write"
    return None


def supervisor(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    history = list(state.get("subagent_history") or [])
    counts = {
        "researcher": history.count("researcher"),
        "coder":      history.count("coder"),
        "writer":     history.count("writer"),
    }

    # 1) Hard short-circuit on definitive state (cheap, deterministic).
    forced = _force_route(history)
    if forced is not None:
        emit("supervisor", "subagent",
             {"agent": "supervisor", "next": forced, "forced": True,
              "history": history, "counts": counts}, session_id=sid)
        return {"route": forced}

    # 2) Otherwise consult the fast LLM with explicit history + caps in prompt.
    msgs = state.get("messages") or []
    transcript = "\n\n".join(
        f"[{type(m).__name__}] " + (m.content if isinstance(m.content, str) else str(m.content))[:500]
        for m in msgs[-8:]
    )
    plan = "\n".join(f"- {s}" for s in state.get("plan", []))
    history_blob = (
        "(none — first hop)" if not history
        else " → ".join(history) + f"   counts={counts}"
    )

    llm = get_fast_llm(temperature=0).with_structured_output(Decision)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SUPERVISOR_PROMPT),
        ("human",
         "USER:\n{q}\n\nPLAN:\n{plan}\n\n"
         "ALREADY RAN:\n{hist}\n\n"
         "TRANSCRIPT:\n{tr}\n\nNext step?"),
    ])
    try:
        d: Decision = (prompt | llm).invoke({
            "q": state.get("user_input", ""),
            "plan": plan,
            "hist": history_blob,
            "tr": transcript,
        })
        nxt = d.next if d.next in ("research", "code", "write", "done") else "write"
    except Exception as e:
        emit("supervisor", "error", {"error": repr(e)}, session_id=sid)
        nxt = "write"

    # 3) Belt-and-braces: even if the LLM picks a specialist that's
    # already capped, override it.
    if nxt == "research" and counts["researcher"] >= MAX_PER_SPECIALIST:
        nxt = "code" if counts["coder"] < MAX_PER_SPECIALIST else "write"
    elif nxt == "code" and counts["coder"] >= MAX_PER_SPECIALIST:
        nxt = "research" if counts["researcher"] < MAX_PER_SPECIALIST else "write"
    elif nxt == "done" and "writer" not in history:
        # "done" before writer ran makes no sense — force writer first.
        nxt = "write"

    emit("supervisor", "subagent",
         {"agent": "supervisor", "next": nxt, "history": history, "counts": counts},
         session_id=sid)
    return {"route": nxt}


def route_supervisor(state: AgentState):
    nxt = state.get("route", "write")
    if nxt == "research":
        return "researcher"
    if nxt == "code":
        return "coder"
    if nxt == "write":
        return "writer"
    return "critic"  # "done"
