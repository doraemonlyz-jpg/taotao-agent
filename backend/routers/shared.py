"""Cross-router shared state · injected by app.py at startup.

Why this exists: slowapi's `@limiter.limit("...")` decorator MUST be
applied at function-definition time (when the router module is imported).
That means the Limiter object has to exist BEFORE the router file is
imported.

App.py wires this up by:
  1. install_security(app) → sets app.state.limiter
  2. set_limiter(app.state.limiter) on this module
  3. import the routers (they read _LIMITER at decoration time)
  4. app.include_router(...)

If you reverse 2 and 3, every chat endpoint silently runs without rate
limits · that's the bug this module's existence prevents.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_LIMITER: Any = None  # slowapi.Limiter | None


def set_limiter(limiter: Any) -> None:
    """Called once by app.py after install_security."""
    global _LIMITER
    _LIMITER = limiter


def maybe_rate_limit(limit_str: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory · noop in dev (no slowapi), real limit in prod.

    Captures the current value of `_LIMITER` at decoration time, which is
    why `set_limiter()` must run before any router that uses this is imported.
    """
    limiter = _LIMITER

    def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        if limiter is None:
            return fn
        return limiter.limit(limit_str)(fn)  # type: ignore[no-any-return]

    return _wrap
