"""Shared dataclasses for the multi-agent package."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentSpec:
    """One participant in a multi-agent run.

    `name` is what the model sees in transcripts ("optimist", "skeptic",
    "reviewer-1").  `system` is its personality/role prompt.  `model` is
    optional · `None` means use the default (settings.model).
    """
    name: str
    system: str
    model: str | None = None


@dataclass
class Result:
    """Uniform return shape for every pattern.

    `final` is the answer to surface to the user.
    `trace` is a list of {"agent": str, "round": int, "text": str} events
    for debugging / writing eval golden files.
    `meta` holds pattern-specific stats (rounds, votes, handoff path).
    """
    final: str
    trace: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
