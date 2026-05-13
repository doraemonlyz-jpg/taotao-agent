"""Billing endpoints · Stripe checkout, portal, webhook, plan inspect.

  GET  /billing/plans                    · public · catalog (id/name/price/quotas)
  GET  /billing/me                       · current tenant's plan + period
  POST /billing/checkout                 · start Stripe Checkout · returns url
  POST /billing/portal                   · open Stripe customer portal · returns url
  POST /billing/webhook                  · Stripe → us · subscription/invoice events

The webhook is the only endpoint Stripe directly calls · it verifies
the signature using STRIPE_WEBHOOK_SECRET, dedups by event id, then
updates the tenant row.

In dev, run `stripe listen --forward-to localhost:8000/billing/webhook`
which will print a `whsec_*` you copy into STRIPE_WEBHOOK_SECRET.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from agent.auth import Identity, current_identity
from agent.billing import PLANS, get_tenant, plan_for, upsert_tenant
from agent.billing.models import record_event
from agent.billing.stripe_client import (
    create_checkout_session,
    is_configured,
    open_customer_portal,
    verify_webhook_signature,
)

log = logging.getLogger("agent.billing")

router = APIRouter(prefix="/billing", tags=["billing"])


# --------------------------------------------------------------------- #
# Public · plan catalog
# --------------------------------------------------------------------- #
@router.get("/plans")
def plans() -> list[dict]:
    """Return the plan catalog as plain JSON · for pricing-page rendering."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "price_usd_per_month": p.price_usd_per_month,
            "included_tokens": p.included_tokens,
            "overage_per_1m_usd": p.overage_per_1m_usd,
            "daily_token_cap": p.daily_token_cap,
        }
        for p in PLANS.values()
    ]


@router.get("/me")
def my_billing(ident: Identity = Depends(current_identity)) -> dict:
    """Return the current tenant's plan + Stripe linkage.

    Auto-creates a `tenants` row at Free tier on first call · so every
    tenant has a billing record from day 1.
    """
    row = get_tenant(ident.tenant_id) or upsert_tenant(tenant_id=ident.tenant_id)
    plan = plan_for(row.get("plan_id"))
    return {
        "tenant_id": ident.tenant_id,
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "price_usd_per_month": plan.price_usd_per_month,
            "included_tokens": plan.included_tokens,
            "daily_token_cap": plan.daily_token_cap,
        },
        "stripe": {
            "configured": is_configured(),
            "customer_id": row.get("stripe_customer_id"),
            "subscription_id": row.get("stripe_subscription_id"),
            "current_period_end": row.get("current_period_end"),
        },
    }


# --------------------------------------------------------------------- #
# Checkout · upgrade flow
# --------------------------------------------------------------------- #
class CheckoutIn(BaseModel):
    """Start a Stripe Checkout session for a paid plan.

    `success_url` and `cancel_url` are where Stripe sends the user after
    they pay (or bail).  Frontend typically uses
    `window.location.href + "?billing=success"`.
    """

    plan_id: str  # one of PLANS.keys() · 'starter' / 'pro' / 'enterprise'
    success_url: str
    cancel_url: str


@router.post("/checkout")
def checkout(
    p: CheckoutIn,
    ident: Identity = Depends(current_identity),
) -> dict:
    if not is_configured():
        raise HTTPException(503, "Stripe not configured · set STRIPE_API_KEY")
    plan = plan_for(p.plan_id)
    if plan.id == "free":
        raise HTTPException(400, "Cannot checkout for free plan")
    if not plan.stripe_price_id:
        raise HTTPException(
            500,
            f"Plan {plan.id!r} has no stripe_price_id configured · "
            "set STRIPE_PRICE_{PLAN}=price_xxx in env and rebuild PLANS table",
        )
    url = create_checkout_session(
        tenant_id=ident.tenant_id,
        plan_price_id=plan.stripe_price_id,
        success_url=p.success_url,
        cancel_url=p.cancel_url,
        customer_email=ident.email,
    )
    return {"url": url}


class PortalIn(BaseModel):
    return_url: str


@router.post("/portal")
def portal(
    p: PortalIn,
    ident: Identity = Depends(current_identity),
) -> dict:
    if not is_configured():
        raise HTTPException(503, "Stripe not configured")
    row = get_tenant(ident.tenant_id) or {}
    customer_id = row.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(404, "No Stripe customer · subscribe first")
    return {"url": open_customer_portal(customer_id=customer_id, return_url=p.return_url)}


# --------------------------------------------------------------------- #
# Webhook · Stripe → us
# --------------------------------------------------------------------- #
@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None),
) -> dict:
    """Receive subscription / invoice / payment events from Stripe.

    We MUST:
      1. Verify the signature (or any internet rando can promote tenants to Pro)
      2. Dedupe by event_id (Stripe retries on 5xx · we'd double-process)
      3. Return 200 fast · Stripe retries on slow handlers (>30s timeout)

    The events we care about:
      - checkout.session.completed         · user just paid · link customer
      - customer.subscription.created      · new subscription · set plan
      - customer.subscription.updated      · plan change · update tier
      - customer.subscription.deleted      · cancel · downgrade to free
      - invoice.payment_failed             · dunning · log + maybe email
    """
    if not is_configured():
        raise HTTPException(503, "Stripe not configured · webhook ignored")

    raw_body = await request.body()
    try:
        event = verify_webhook_signature(raw_body, stripe_signature)
    except Exception as e:
        log.warning("Stripe webhook signature failed: %s", e)
        raise HTTPException(400, f"signature verification failed: {e}") from e

    event_id = str(event.get("id"))
    event_type = str(event.get("type"))
    obj: dict[str, Any] = (event.get("data") or {}).get("object") or {}

    tenant_id = _tenant_id_from_object(obj)

    # Idempotency · skip if seen
    is_new = record_event(
        stripe_event_id=event_id,
        event_type=event_type,
        tenant_id=tenant_id,
        payload_json=json.dumps(event, default=str),
    )
    if not is_new:
        log.info("stripe webhook duplicate · event=%s", event_id)
        return {"ok": True, "duplicate": True}

    log.info("stripe webhook · type=%s tenant=%s event=%s", event_type, tenant_id, event_id)

    # Dispatch · only handle the events we know about · ignore the rest
    # (Stripe sends ~50 event types · we don't need most of them).
    if event_type == "checkout.session.completed":
        if tenant_id and obj.get("customer"):
            upsert_tenant(
                tenant_id=tenant_id,
                stripe_customer_id=str(obj["customer"]),
                stripe_subscription_id=obj.get("subscription"),
            )

    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        if tenant_id:
            plan_id = _plan_id_from_subscription(obj)
            upsert_tenant(
                tenant_id=tenant_id,
                plan_id=plan_id,
                stripe_subscription_id=str(obj.get("id") or ""),
                stripe_customer_id=obj.get("customer"),
                current_period_start=_iso_from_unix(obj.get("current_period_start")),
                current_period_end=_iso_from_unix(obj.get("current_period_end")),
            )

    elif event_type == "customer.subscription.deleted":
        if tenant_id:
            upsert_tenant(tenant_id=tenant_id, plan_id="free", stripe_subscription_id="")

    elif event_type == "invoice.payment_failed":
        # Just log · Stripe handles dunning for us.  Wire to alerting here.
        log.warning("invoice.payment_failed · tenant=%s · check Stripe dashboard", tenant_id)

    return {"ok": True, "event": event_type}


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #
def _tenant_id_from_object(obj: dict) -> str | None:
    """Extract our tenant_id from a Stripe payload.

    Stripe's `metadata` field is the source of truth · we set it during
    checkout (`metadata={"tenant_id": ...}`).  Fallback to
    `client_reference_id` for the checkout.session event.
    """
    meta = obj.get("metadata") or {}
    if meta.get("tenant_id"):
        return str(meta["tenant_id"])
    if obj.get("client_reference_id"):
        return str(obj["client_reference_id"])
    return None


def _plan_id_from_subscription(sub: dict) -> str | None:
    """Map a Stripe subscription payload to one of our PLANS ids.

    We compare against `stripe_price_id` on each Plan · the first match
    wins.  Returns None when no plan matches (typically means the
    subscription is on a price we don't know about · admin should sync
    PLANS table).
    """
    items = (sub.get("items") or {}).get("data") or []
    if not items:
        return None
    price_id = ((items[0] or {}).get("price") or {}).get("id")
    for p in PLANS.values():
        if p.stripe_price_id and p.stripe_price_id == price_id:
            return p.id
    return None


def _iso_from_unix(ts: int | float | None) -> str | None:
    """Convert a Stripe unix timestamp to ISO-8601 (or None)."""
    if not ts:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
