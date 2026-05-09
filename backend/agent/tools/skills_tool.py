"""Tool that loads a procedural-memory skill on demand."""
from __future__ import annotations

from langchain_core.tools import tool

from ..memory.skills import get_skill, list_skills


@tool
def load_skill(name: str) -> str:
    """Fetch the full body of a named skill (a procedural-memory recipe).

    The system prompt already lists every available skill's name + short
    description. Call this when you decide one of them applies, to pull in
    the detailed step-by-step instructions before acting.

    Args:
        name: The exact skill name (case-sensitive, as shown in the index).
    """
    s = get_skill(name)
    if not s:
        avail = ", ".join(sk.name for sk in list_skills()) or "(none defined)"
        return f"No skill named '{name}'. Available: {avail}"
    return f"# Skill: {s.name}\n\n{s.body}"
