"""Component 4 — Planning.

Decides between two routes, both well-known patterns:

  - "direct"     → ReAct loop (planner=executor; one model picks tool then acts)
  - "supervisor" → plan-and-execute via specialist sub-agents

Heuristic only — keeps the demo self-contained. A real system would learn
this routing from traces."""
from __future__ import annotations

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from ..observability import emit
from ..state import AgentState
from .llm import get_fast_llm

# Trivially-routable prefixes / patterns — skip the LLM router for these
TRIVIAL_PREFIXES = (
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
    "你好", "谢谢", "好的", "嗨", "你是谁", "what are you",
)

ROUTER_PROMPT = """You are a routing classifier for an AI agent.

Given the user's message, decide:
  - "direct": single-shot or tool-light. Answer with a normal ReAct loop.
  - "supervisor": multi-step task that benefits from specialist sub-agents
                  (research + code + writing).

Reply with the route only. Then list 2-5 concrete sub-steps for solving it.
"""


class Route(BaseModel):
    route: str = Field(description="Either 'direct' or 'supervisor'")
    plan: list[str] = Field(description="2-5 numbered sub-steps")


def _trivial(user_text: str) -> bool:
    """Heuristic short-circuit so we don't spend an LLM call on `hi`."""
    t = user_text.lower().strip()
    if len(t) < 4:
        return True
    if any(t.startswith(p) for p in TRIVIAL_PREFIXES) and len(t) < 30:
        return True
    return False


def planner(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    if state.get("blocked"):
        return {}

    user_text = state.get("user_input", "")

    # Heuristic short-circuit — saves an LLM call on chitchat
    if _trivial(user_text):
        emit("planner", "plan",
             {"route": "direct", "plan": ["respond directly"], "shortcut": True},
             session_id=sid)
        return {"route": "direct", "plan": [user_text], "current_step": 0}

    structured = get_fast_llm(temperature=0).with_structured_output(Route)
    prompt = ChatPromptTemplate.from_messages([
        ("system", ROUTER_PROMPT),
        ("human", "{q}"),
    ])
    try:
        result: Route = (prompt | structured).invoke({"q": user_text})
        route = result.route if result.route in ("direct", "supervisor") else "direct"
        plan = result.plan or [user_text]
    except Exception as e:
        # fall back to direct mode if structured output fails
        emit("planner", "error", {"error": repr(e)}, session_id=sid)
        route, plan = "direct", [user_text]

    emit("planner", "plan", {"route": route, "plan": plan}, session_id=sid)
    return {"route": route, "plan": plan, "current_step": 0}


def route_after_planner(state: AgentState):
    """Conditional edge — go to executor (direct) or supervisor."""
    if state.get("blocked"):
        return "output_guardrail"
    return "supervisor" if state.get("route") == "supervisor" else "executor"
