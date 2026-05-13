"""Multi-agent reference implementations · 4 production patterns.

This package is the *opinionated* home for "more than one LLM in a loop"
patterns that don't fit the single-agent harness or the supervisor graph.

When to reach in here (rare · default = single agent):
  - Hard reasoning where N-vote / debate measurably beats single-shot
  - User-facing tasks where two specialised models hand off cleanly
  - Iteration loops (actor + critic) for code / writing quality

Patterns implemented:

  - `debate.run_debate`        · N agents argue until consensus or max-rounds
  - `voting.run_vote`          · N agents answer independently, majority wins
  - `handoff.run_handoff`      · OpenAI-Swarm-style explicit control transfer
  - `reflection.run_reflection`· Reflexion / Self-Refine actor + critic loop

All four return a `Result` dataclass and emit trace events under node
`multi_agent` so the existing observability bus picks them up.

Usage from the harness · use the wrapper tool `multi_agent_run` in
`agent/tools/multi_agent_tool.py` so the LLM can pick a pattern at runtime.
"""
from __future__ import annotations

from .debate import run_debate
from .handoff import run_handoff
from .reflection import run_reflection
from .types import AgentSpec, Result
from .voting import run_vote

__all__ = [
    "AgentSpec",
    "Result",
    "run_debate",
    "run_handoff",
    "run_reflection",
    "run_vote",
]
