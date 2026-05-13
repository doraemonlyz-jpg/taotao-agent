"""Per-user token / USD quota · sqlite-backed · enforced before every chat.

Pricing model:
  - Each (tenant_id, user_id, period) row in `quota_usage` accumulates
    tokens + cost_usd + request count.
  - Two periods: `day:YYYY-MM-DD` and `month:YYYY-MM`.  Rolling windows
    are not used · billing periods are calendar-aligned which matches
    every B2B SaaS pricing page on Earth.
  - Caps come from env (QUOTA_DAILY_TOKENS / QUOTA_MONTHLY_TOKENS /
    QUOTA_DAILY_USD / QUOTA_MONTHLY_USD).  Per-tenant / per-plan caps
    are out of scope for the starter · plug a `plans` table here when
    you wire Stripe (Round 5, see routers/billing.py).

Why sqlite and not Postgres for the starter:
  - Zero ops · works in CI · keeps the demo deployable from `make dev`.
  - Round 4 (P3.12) introduces the Postgres docker-compose service · at
    that point you'd swap the DSN here too. The schema is intentionally
    Postgres-compatible · no sqlite-isms in the SQL.

Why not just OpenTelemetry metrics:
  - We need to ENFORCE on the request path (429 on quota exhaustion).
    OTel metrics flow OUT, async, batched · wrong primitive for guardrails.
  - We still emit OTel metrics in parallel (telemetry.py) for dashboards.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status

from .auth import Identity, get_current_identity
from .config import get_settings

log = logging.getLogger("agent.quota")

# --------------------------------------------------------------------- #
# DB connection · sqlite for the starter, swap DSN for Postgres later.
# We keep one connection per thread (sqlite is not async-safe) and
# rely on FastAPI's threadpool dispatch for endpoint isolation.
# --------------------------------------------------------------------- #
_local = threading.local()


def _db_path() -> Path:
    """The quota DB lives next to the chroma dir so backup scripts that
    snapshot `data/` capture it automatically."""
    cfg = get_settings()
    base = Path(cfg.chroma_dir).parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "quota.sqlite"


def _conn() -> sqlite3.Connection:
    if getattr(_local, "conn", None) is None:
        c = sqlite3.connect(_db_path(), check_same_thread=False, timeout=5.0)
        c.row_factory = sqlite3.Row
        # WAL + NORMAL · fast enough for 100 req/sec, durable enough for billing.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
        _ensure_schema(c)
    return _local.conn  # type: ignore[no-any-return]


def _ensure_schema(c: sqlite3.Connection) -> None:
    """Idempotent schema migration · DDL only, no data backfill needed."""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS quota_usage (
            tenant_id TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            period    TEXT NOT NULL,           -- 'day:YYYY-MM-DD' | 'month:YYYY-MM'
            tokens    INTEGER NOT NULL DEFAULT 0,
            cost_usd  REAL NOT NULL DEFAULT 0,
            requests  INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, user_id, period)
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_quota_user_period
        ON quota_usage(user_id, period)
        """
    )
    c.commit()


@contextmanager
def _tx():
    """Mini transaction wrapper · commits on success, rolls back on raise."""
    c = _conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


# --------------------------------------------------------------------- #
# Period helpers
# --------------------------------------------------------------------- #
def _today_period() -> str:
    return f"day:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _this_month_period() -> str:
    return f"month:{datetime.now(timezone.utc).strftime('%Y-%m')}"


# --------------------------------------------------------------------- #
# Env-driven caps
# --------------------------------------------------------------------- #
def _enabled() -> bool:
    raw = (os.environ.get("QUOTA_ENABLED") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _cap_int(env_name: str) -> int:
    """Env var as integer · 0 / unset means unlimited."""
    raw = (os.environ.get(env_name) or "0").strip()
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        return 0


def _cap_float(env_name: str) -> float:
    raw = (os.environ.get(env_name) or "0").strip()
    try:
        v = float(raw)
        return max(0.0, v)
    except ValueError:
        return 0.0


# --------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------- #
def record_usage(
    *,
    tenant_id: str,
    user_id: str,
    tokens: int,
    cost_usd: float,
) -> None:
    """Add tokens + cost to BOTH the daily and monthly counters.

    Called at end of each chat turn (or on every UsageCallback fire ·
    you pick).  Idempotent at the DB level · upsert on conflict.
    """
    if not _enabled():
        return
    if tokens <= 0 and cost_usd <= 0:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _tx() as c:
        for period in (_today_period(), _this_month_period()):
            c.execute(
                """
                INSERT INTO quota_usage (tenant_id, user_id, period, tokens, cost_usd, requests, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(tenant_id, user_id, period) DO UPDATE SET
                    tokens   = tokens   + excluded.tokens,
                    cost_usd = cost_usd + excluded.cost_usd,
                    requests = requests + excluded.requests,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, user_id, period, tokens, cost_usd, now),
            )


def snapshot(*, tenant_id: str, user_id: str) -> dict[str, Any]:
    """Return the current period usage + caps.

    Shape:
      {
        "enabled": bool,
        "user_id": str, "tenant_id": str,
        "day":   {"period": str, "tokens": int, "cost_usd": float, "requests": int,
                  "cap_tokens": int, "cap_usd": float, "exhausted": bool},
        "month": {... same shape ...},
      }
    """
    out: dict[str, Any] = {
        "enabled": _enabled(),
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    if not _enabled():
        return out

    c = _conn()
    for slot, period, cap_t_env, cap_u_env in (
        ("day", _today_period(), "QUOTA_DAILY_TOKENS", "QUOTA_DAILY_USD"),
        ("month", _this_month_period(), "QUOTA_MONTHLY_TOKENS", "QUOTA_MONTHLY_USD"),
    ):
        row = c.execute(
            "SELECT tokens, cost_usd, requests FROM quota_usage "
            "WHERE tenant_id=? AND user_id=? AND period=?",
            (tenant_id, user_id, period),
        ).fetchone()
        tokens = int(row["tokens"]) if row else 0
        cost = float(row["cost_usd"]) if row else 0.0
        reqs = int(row["requests"]) if row else 0
        cap_t = _cap_int(cap_t_env)
        cap_u = _cap_float(cap_u_env)
        out[slot] = {
            "period": period,
            "tokens": tokens,
            "cost_usd": round(cost, 6),
            "requests": reqs,
            "cap_tokens": cap_t,
            "cap_usd": cap_u,
            "exhausted": _is_exhausted(tokens, cost, cap_t, cap_u),
        }
    return out


def _is_exhausted(tokens: int, cost: float, cap_t: int, cap_u: float) -> bool:
    if cap_t > 0 and tokens >= cap_t:
        return True
    if cap_u > 0 and cost >= cap_u:
        return True
    return False


def check_quota(ident: Identity) -> None:
    """Raise 429 when the caller is out of budget for either day or month.

    Cheap · single SELECT per period.  Safe to call on every request.
    Skipped entirely when QUOTA_ENABLED is off.
    """
    if not _enabled():
        return
    snap = snapshot(tenant_id=ident.tenant_id, user_id=ident.user_id)
    for slot in ("day", "month"):
        s = snap.get(slot) or {}
        if s.get("exhausted"):
            log.warning(
                "quota exhausted · tenant=%s user=%s slot=%s tokens=%s cost=%s",
                ident.tenant_id, ident.user_id, slot, s.get("tokens"), s.get("cost_usd"),
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": f"{slot} quota exhausted",
                    "slot": slot,
                    "spent_tokens": s.get("tokens"),
                    "spent_usd": s.get("cost_usd"),
                    "cap_tokens": s.get("cap_tokens"),
                    "cap_usd": s.get("cap_usd"),
                    "hint": "Upgrade plan or wait until next period.",
                },
            )


# --------------------------------------------------------------------- #
# FastAPI dependency · drop into /chat endpoints
# --------------------------------------------------------------------- #
async def enforce_user_quota(request: Request) -> None:
    """FastAPI dependency · 429 when over budget.

    Use:
        @router.post("/chat", dependencies=[Depends(enforce_user_quota)])

    Reads identity from the request-scoped ContextVar (set by the
    identity middleware in app.py) so it works for both API_KEY mode
    (shared identity) and JWT mode (per-user identity).
    """
    if not _enabled():
        return
    ident = get_current_identity()
    check_quota(ident)


# --------------------------------------------------------------------- #
# Test / admin helpers
# --------------------------------------------------------------------- #
def reset_for_user(*, tenant_id: str, user_id: str) -> dict[str, int]:
    """Wipe both daily and monthly counters for one user.

    Returns row counts deleted per period.  Used by /admin/quota/reset
    and by test fixtures (resetting between cases).
    """
    out: dict[str, int] = {}
    with _tx() as c:
        for slot, period in (("day", _today_period()), ("month", _this_month_period())):
            cur = c.execute(
                "DELETE FROM quota_usage WHERE tenant_id=? AND user_id=? AND period=?",
                (tenant_id, user_id, period),
            )
            out[slot] = cur.rowcount
    return out
