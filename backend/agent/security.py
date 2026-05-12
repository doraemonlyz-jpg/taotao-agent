"""
Security primitives · auth / rate-limit / sentry init.

All three are **env-gated** so localhost dev keeps working with zero
config:

  - API_KEY unset            → auth disabled (any caller passes)
  - RATE_LIMIT_ENABLED=0      → rate limit disabled
  - SENTRY_DSN unset          → sentry no-ops

Production deploy = set the env vars and restart. No code changes.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

log = logging.getLogger("agent.security")

# ------------------------------------------------------------------ #
# 1 · Auth · X-API-Key header
# ------------------------------------------------------------------ #
# We use APIKeyHeader so it shows up nicely in /docs (Swagger gets a
# little "Authorize" lock icon and remembers the key for the session).
_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # we return 401 ourselves with a friendlier body
    description="Shared API key. Skip if API_KEY env var is unset (dev).",
)


def _expected_key() -> str | None:
    """Return the configured API key, or None if auth is disabled.

    `API_KEY=""` is also treated as disabled so users can keep the var
    in `.env.example` without accidentally locking themselves out.
    """
    v = os.environ.get("API_KEY") or ""
    return v.strip() or None


async def require_api_key(
    key: str | None = Security(_api_key_header),
) -> None:
    """FastAPI dependency for protected endpoints.

    Use as `Depends(require_api_key)` on any mutating endpoint
    (POST /chat, POST /model, DELETE /memory, …). Reading the key via
    `Security(APIKeyHeader)` makes Swagger render an "Authorize" button.

    Behaviour:
      - API_KEY env unset → no-op (dev friendly)
      - key matches       → no-op
      - key missing/wrong → 401
    """
    expected = _expected_key()
    if expected is None:
        return  # auth disabled
    if key == expected:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid X-API-Key header",
        headers={"WWW-Authenticate": "ApiKey"},
    )


# ------------------------------------------------------------------ #
# 2 · Rate limit · slowapi (in-memory · per-IP)
# ------------------------------------------------------------------ #
# slowapi is a Starlette-friendly wrapper around limits. Defaults are
# conservative; bumps via env so an interview demo with 5 reviewers
# clicking around doesn't get cut off.
try:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address

    _SLOWAPI_AVAILABLE = True
except Exception:  # pragma: no cover
    _SLOWAPI_AVAILABLE = False


def _rate_enabled() -> bool:
    """Default ON when slowapi installed; explicit `RATE_LIMIT_ENABLED=0` disables."""
    if not _SLOWAPI_AVAILABLE:
        return False
    v = os.environ.get("RATE_LIMIT_ENABLED", "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def build_limiter():  # type: ignore[no-untyped-def]
    """Construct a slowapi Limiter pinned to client IP."""
    if not _rate_enabled():
        return None
    chat_lim = os.environ.get("RATE_LIMIT_CHAT", "60/minute")
    read_lim = os.environ.get("RATE_LIMIT_READ", "600/minute")
    log.info("rate-limit: chat=%s read=%s", chat_lim, read_lim)
    # slowapi `default_limits` apply to every endpoint w/o a specific decorator.
    return Limiter(
        key_func=get_remote_address,
        default_limits=[read_lim],
        storage_uri="memory://",
        headers_enabled=True,
    )


def install_rate_limit(app: FastAPI, limiter) -> None:
    """Mount slowapi's middleware + 429 handler onto the app."""
    if limiter is None:
        return
    from slowapi.middleware import SlowAPIMiddleware

    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_handler(request: Request, exc: RateLimitExceeded):  # type: ignore[unused-ignore]
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={
                "detail": "rate limit exceeded",
                "limit": str(exc.detail),
                "retry_after_seconds": getattr(exc, "retry_after", None),
            },
        )


def chat_rate_limit() -> str:
    """Limit string for /chat endpoints (override via RATE_LIMIT_CHAT env)."""
    return os.environ.get("RATE_LIMIT_CHAT", "60/minute")


# ------------------------------------------------------------------ #
# 3 · Sentry · error tracking + perf
# ------------------------------------------------------------------ #
def init_sentry() -> bool:
    """Initialise sentry-sdk if SENTRY_DSN is set. No-op otherwise.

    Returns True if Sentry was actually wired (useful for /health).
    """
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        env = os.environ.get("SENTRY_ENV", "dev")
        traces_sample_rate = float(
            os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")
        )
        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            traces_sample_rate=traces_sample_rate,
            send_default_pii=False,
            integrations=[
                StarletteIntegration(),
                FastApiIntegration(),
            ],
        )
        log.info("sentry initialised · env=%s sample=%s", env, traces_sample_rate)
        return True
    except Exception as e:  # pragma: no cover · degrade gracefully
        log.warning("sentry init failed: %s", e)
        return False


# ------------------------------------------------------------------ #
# 4 · Convenience: wire all three into a fresh FastAPI app
# ------------------------------------------------------------------ #
def install_security(app: FastAPI) -> dict:
    """One-call setup. Returns a status dict for /health to surface."""
    sentry_on = init_sentry()
    limiter = build_limiter()
    install_rate_limit(app, limiter)
    return {
        "auth_required": _expected_key() is not None,
        "rate_limit_enabled": limiter is not None,
        "sentry_enabled": sentry_on,
    }
