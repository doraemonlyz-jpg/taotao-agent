"""Tenant + billing schema · sqlite-backed (Postgres-compatible DDL).

Two tables:

  tenants       · current plan + Stripe customer/subscription id
  usage_events  · audit log of every webhook delivery (for debugging
                  + double-write protection · we de-dupe by stripe_event_id)

Why sqlite for the skeleton: keeps `make dev` working with no infra.
For real Stripe billing you SHOULD swap to Postgres (the schema is
already pg-compatible · only need to change the connect string).
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings

_local = threading.local()


def _db_path() -> Path:
    cfg = get_settings()
    base = Path(cfg.chroma_dir).parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "billing.sqlite"


def _conn() -> sqlite3.Connection:
    if getattr(_local, "conn", None) is None:
        c = sqlite3.connect(_db_path(), check_same_thread=False, timeout=5.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
        ensure_schema()
    return _local.conn  # type: ignore[no-any-return]


def ensure_schema() -> None:
    """Idempotent · safe to call from app startup AND from tests."""
    c = (
        _local.conn
        if getattr(_local, "conn", None) is not None
        else sqlite3.connect(_db_path(), check_same_thread=False)
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id              TEXT PRIMARY KEY,
            plan_id                TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id     TEXT,
            stripe_subscription_id TEXT,
            current_period_start   TEXT,
            current_period_end     TEXT,
            created_at             TEXT NOT NULL,
            updated_at             TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tenants_stripe_customer
        ON tenants(stripe_customer_id)
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            stripe_event_id  TEXT PRIMARY KEY,        -- prevents double-process
            event_type       TEXT NOT NULL,
            tenant_id        TEXT,
            payload_json     TEXT NOT NULL,
            received_at      TEXT NOT NULL
        )
        """
    )
    c.commit()


@contextmanager
def _tx():
    c = _conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


# --------------------------------------------------------------------- API


def upsert_tenant(
    *,
    tenant_id: str,
    plan_id: str | None = None,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    current_period_start: str | None = None,
    current_period_end: str | None = None,
) -> dict[str, Any]:
    """Create the tenant row if missing · update the named fields if not.

    Used by:
      - bootstrap (first /chat from a fresh tenant · default to free)
      - webhook (subscription.created/updated)
      - admin tools (manual plan change after refund/promo/etc.)
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _tx() as c:
        existing = c.execute(
            "SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        if existing is None:
            c.execute(
                """
                INSERT INTO tenants (
                    tenant_id, plan_id, stripe_customer_id, stripe_subscription_id,
                    current_period_start, current_period_end, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id, plan_id or "free", stripe_customer_id,
                    stripe_subscription_id, current_period_start,
                    current_period_end, now, now,
                ),
            )
        else:
            sets = []
            args: list[Any] = []
            for col, val in (
                ("plan_id", plan_id),
                ("stripe_customer_id", stripe_customer_id),
                ("stripe_subscription_id", stripe_subscription_id),
                ("current_period_start", current_period_start),
                ("current_period_end", current_period_end),
            ):
                if val is not None:
                    sets.append(f"{col}=?")
                    args.append(val)
            sets.append("updated_at=?")
            args.append(now)
            args.append(tenant_id)
            c.execute(
                f"UPDATE tenants SET {', '.join(sets)} WHERE tenant_id=?", args
            )
    return get_tenant(tenant_id) or {}


def get_tenant(tenant_id: str) -> dict[str, Any] | None:
    """Return the tenant row as a dict, or None if not found."""
    c = _conn()
    row = c.execute("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()
    return dict(row) if row else None


def list_tenants() -> list[dict[str, Any]]:
    """All tenants · used by /admin/billing/tenants."""
    c = _conn()
    rows = c.execute("SELECT * FROM tenants ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def record_event(
    *,
    stripe_event_id: str,
    event_type: str,
    tenant_id: str | None,
    payload_json: str,
) -> bool:
    """Persist a Stripe webhook event for audit + double-process protection.

    Returns True if newly inserted, False if we'd seen this event_id
    already (Stripe retries on 5xx · idempotency is on US).
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with _tx() as c:
            c.execute(
                """
                INSERT INTO usage_events (
                    stripe_event_id, event_type, tenant_id, payload_json, received_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (stripe_event_id, event_type, tenant_id, payload_json, now),
            )
        return True
    except sqlite3.IntegrityError:
        return False  # already processed
