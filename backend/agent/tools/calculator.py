"""Deterministic math via numexpr — sandboxed, no `eval`."""
from __future__ import annotations

import numexpr
from langchain_core.tools import tool


@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression. Supports +,-,*,/,**, sqrt, log, sin, cos, etc.
    Use this whenever the user asks anything quantitative. NEVER do arithmetic
    in your head — it's unreliable.

    Args:
        expression: A python/numexpr expression, e.g. "2 ** 10 * sin(0.5)"
    """
    try:
        result = numexpr.evaluate(expression).item()
        return f"{expression} = {result}"
    except Exception as e:
        return f"Calculator error: {e!r}. Check the expression syntax."
