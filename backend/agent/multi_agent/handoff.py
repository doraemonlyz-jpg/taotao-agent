"""Multi-agent **handoff** · OpenAI-Swarm-style explicit control transfer.

Mental model (Swarm, Oct 2024 reference):
  - Each agent has a list of tools AND a list of `transfer_to_<name>` tools.
  - When an agent decides another agent is better suited, it calls the
    transfer tool · the *whole conversation* moves to the new agent.
  - No supervisor LLM in the middle · cheaper than hierarchical routing.
  - Termination: any agent that returns text instead of a transfer tool.

Reference: https://github.com/openai/swarm  (the "Routines & Handoffs" pattern)
A production example is the customer-service router used by Stripe / Shopify
demos · triage agent → billing agent → refund agent → confirm.

Why use it (real production case):
  - Multi-domain customer support · each agent knows its narrow vertical.
  - Workflow automation where step boundaries are clean (intake → review
    → approve → execute).
  - Code-as-policy systems where an agent can hand off based on data shape.

When NOT to use:
  - Anything that fits a single agent + tools.
  - Tasks needing global memory across all agents (handoff loses focus).

This implementation is intentionally simple · 1 file · no langchain
agent abstractions · pure dict + while-loop · ~120 lines.
"""
from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
)

from ..nodes.llm import get_llm
from ..observability import emit
from .types import AgentSpec, Result

# ----------------------------------------------------------------- agents


def _agent_with_handoffs(
    spec: AgentSpec,
    other_names: list[str],
) -> dict:
    """Render a system prompt that lists transfer tools as bullet points.

    We don't bind real `BaseTool` objects · we tell the LLM to emit a
    line like  `TRANSFER: <agent-name>  REASON: <why>` and parse it.
    Cheaper than the full tool-call dance for this small pattern.
    """
    handoff_block = ""
    if other_names:
        handoff_block = (
            "\n\nOther agents you can hand off to:\n" +
            "\n".join(f"  - {n}" for n in other_names) +
            "\n\nIf you decide another agent is better suited, end your "
            "reply with a single line:\n"
            "TRANSFER: <agent-name>\n"
            "REASON: <one sentence>\n\n"
            "Otherwise just answer the user · no transfer line."
        )
    return {"name": spec.name,
            "system": spec.system + handoff_block}


def _parse_transfer(text: str) -> str | None:
    """Return target agent name if the text ends with TRANSFER: ..., else None."""
    for ln in text.splitlines()[-6:]:  # only check tail
        s = ln.strip()
        if s.upper().startswith("TRANSFER:"):
            return s.split(":", 1)[1].strip()
    return None


# ----------------------------------------------------------------- runner


def run_handoff(
    task: str,
    agents: list[AgentSpec],
    *,
    entry: str | None = None,
    max_hops: int = 6,
    session_id: str = "",
) -> Result:
    """Drive a Swarm-style handoff loop.

    Args:
      agents: 2-N AgentSpec; the first one is the entry point unless
              `entry` is set explicitly.
      entry: name of the agent to start with.  If unset, agents[0].
      max_hops: hard cap on transfers (prevents A↔B ping-pong).

    Returns Result with `meta["path"]` listing the agents in visit order.
    """
    if len(agents) < 2:
        raise ValueError("handoff needs ≥ 2 agents")
    by_name = {a.name: a for a in agents}
    if entry and entry not in by_name:
        raise ValueError(f"entry {entry!r} not in agents {list(by_name)}")
    current = entry or agents[0].name
    rendered = {n: _agent_with_handoffs(a, [m for m in by_name if m != n])
                for n, a in by_name.items()}

    msgs: list[AnyMessage] = [HumanMessage(content=task)]
    path: list[str] = []
    trace: list[dict] = []

    emit("multi_agent", "handoff_start",
         {"entry": current, "agents": list(by_name)}, session_id=session_id)

    for hop in range(max_hops):
        spec = rendered[current]
        path.append(current)
        chat: list[AnyMessage] = [SystemMessage(content=spec["system"]), *msgs]
        out = get_llm(temperature=0.3).invoke(chat)
        text = out.content if isinstance(out.content, str) else str(out.content)
        msgs.append(AIMessage(content=text))
        trace.append({"agent": current, "round": hop + 1, "text": text})
        emit("multi_agent", "handoff_step",
             {"agent": current, "hop": hop + 1, "preview": text[:200]},
             session_id=session_id)

        target = _parse_transfer(text)
        if not target:
            # natural termination · agent answered
            emit("multi_agent", "handoff_done",
                 {"hops": hop + 1, "final_agent": current},
                 session_id=session_id)
            return Result(final=text, trace=trace,
                          meta={"hops": hop + 1, "path": path,
                                "terminator": current})
        if target not in by_name:
            # invalid transfer · let current agent finish next round with hint
            msgs.append(HumanMessage(
                content=f"(SYSTEM: transfer target '{target}' is unknown · "
                        f"valid: {list(by_name)}.  Please answer or pick a valid target.)"))
            continue
        if target == current:
            msgs.append(HumanMessage(
                content="(SYSTEM: you tried to transfer to yourself · please answer.)"))
            continue
        current = target

    # hit the hop cap
    final = (text + "\n\n[handoff hit max_hops · forcing stop]")
    emit("multi_agent", "handoff_capped",
         {"hops": max_hops, "path": path}, session_id=session_id)
    return Result(final=final, trace=trace,
                  meta={"hops": max_hops, "path": path, "terminator": "max_hops"})
