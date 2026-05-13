"""LangChain tool wrapper · expose multi-agent patterns to the harness LLM.

The LLM picks a `pattern` ∈ {debate, vote, handoff, reflection} and a task.
Returns a compact summary so the LLM can reason over it without us
re-pasting the entire transcript (which can be huge).
"""
from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import tool

from ..multi_agent import (
    AgentSpec, run_debate, run_handoff, run_reflection, run_vote,
)


_PATTERNS = ("debate", "vote", "handoff", "reflection")


def _summarise(pattern: str, result) -> str:
    """Compact JSON summary · pattern + final + 200-char preview of trace."""
    body = {
        "pattern": pattern,
        "final": result.final,
        "meta": result.meta,
        "trace_count": len(result.trace),
        "trace_preview": [
            {"agent": e.get("agent"), "round": e.get("round"),
             "text": (e.get("text") or "")[:200]}
            for e in result.trace[-4:]   # only last 4 events
        ],
    }
    return json.dumps(body, ensure_ascii=False, indent=2)


@tool
def multi_agent_run(
    pattern: Literal["debate", "vote", "handoff", "reflection"],
    task: str,
    n: int = 3,
    max_rounds: int = 3,
    pass_score: int = 8,
) -> str:
    """Run an inner multi-agent group on `task` and return the result.

    Pick `pattern` by problem shape:
      - "debate"     · multi-perspective reasoning (pros/cons, policy)
      - "vote"       · finite-answer self-consistency (math, MCQ, yes/no)
      - "handoff"    · multi-domain workflow (triage → specialist → close)
      - "reflection" · code/writing where critic-revise loop helps

    Args:
        pattern: which multi-agent pattern to run · see above for picking guide.
        task: the problem statement passed to the inner agents.
        n: number of voters (vote pattern only) · 3-7 reasonable.
        max_rounds: cap on debate / reflection rounds.
        pass_score: critic threshold for reflection · 8 = strict, 6 = lenient.

    Returns:
        JSON summary string with `final`, `meta`, and a short trace preview.
    """
    if pattern not in _PATTERNS:
        return json.dumps({"error": f"unknown pattern {pattern!r}",
                           "valid": list(_PATTERNS)})
    if pattern == "debate":
        r = run_debate(task, max_rounds=max_rounds)
    elif pattern == "vote":
        r = run_vote(task, n=n)
    elif pattern == "handoff":
        # for the tool · use a generic 2-agent triage demo
        agents = [
            AgentSpec("triage",
                      "You are TRIAGE · classify the request and hand off "
                      "to 'specialist' if it needs deep work, else answer."),
            AgentSpec("specialist",
                      "You are SPECIALIST · write the deep, expert answer."),
        ]
        r = run_handoff(task, agents, max_hops=max(3, max_rounds + 1))
    else:  # reflection
        r = run_reflection(task, max_rounds=max_rounds, pass_score=pass_score)
    return _summarise(pattern, r)
