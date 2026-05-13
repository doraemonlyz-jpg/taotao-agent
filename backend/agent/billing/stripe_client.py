"""Thin wrapper around stripe-python · keeps Stripe coupling localised.

The rest of the codebase MUST go through this module to talk to Stripe ·
that way swapping providers (Paddle / Lemon Squeezy / Polar) is a single
file change instead of a grep across the repo.

stripe-python is a heavy import · we lazy-import it inside each function
so unrelated code paths (tests, CLI, Ollama-only setups) don't pay the
cold-start cost.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("agent.billing.stripe")


def _api_key() -> str | None:
    """Stripe secret key · `sk_test_*` for testmode, `sk_live_*` for prod."""
    v = (os.environ.get("STRIPE_API_KEY") or "").strip()
    return v or None


def _webhook_secret() -> str | None:
    """Webhook signing secret · `whsec_*` from `stripe listen` or dashboard."""
    v = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    return v or None


def is_configured() -> bool:
    """Return True only when BOTH api key + webhook secret are set."""
    return _api_key() is not None and _webhook_secret() is not None


def _stripe():
    """Lazy import + return the configured stripe module."""
    import stripe  # type: ignore[import-untyped]

    key = _api_key()
    if key is None:
        raise RuntimeError(
            "STRIPE_API_KEY not set · cannot call Stripe. "
            "Skip Stripe init in dev by leaving the var blank."
        )
    stripe.api_key = key
    return stripe


def verify_webhook_signature(payload: bytes, sig_header: str | None) -> dict[str, Any]:
    """Return the parsed Stripe event when the signature is valid.

    Raises:
      RuntimeError if STRIPE_WEBHOOK_SECRET unset
      stripe.SignatureVerificationError on bad signature
    """
    secret = _webhook_secret()
    if secret is None:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not set · cannot verify webhook")
    if sig_header is None:
        raise RuntimeError("Missing Stripe-Signature header")
    stripe = _stripe()
    return stripe.Webhook.construct_event(payload, sig_header, secret)  # type: ignore[no-any-return]


def create_checkout_session(
    *,
    tenant_id: str,
    plan_price_id: str,
    success_url: str,
    cancel_url: str,
    customer_email: str | None = None,
) -> str:
    """Start a hosted Stripe Checkout session · returns the URL to redirect to.

    Caller (the billing router) hands the URL to the frontend which
    `window.location = url`s the user to Stripe.  Stripe then redirects
    back to success_url / cancel_url.

    The `tenant_id` flows through `metadata` so the webhook can find
    the tenant when the subscription is created.
    """
    stripe = _stripe()
    sess = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": plan_price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=tenant_id,
        customer_email=customer_email,
        metadata={"tenant_id": tenant_id},
        subscription_data={"metadata": {"tenant_id": tenant_id}},
        allow_promotion_codes=True,
    )
    return str(sess.url)


def report_usage(
    *,
    subscription_item_id: str,
    quantity: int,
    timestamp: int | None = None,
    action: str = "increment",
) -> dict[str, Any]:
    """Push a metered usage record to Stripe.

    Call this from a periodic job (every ~1h is fine) that tallies
    tokens-since-last-report from the quota DB and forwards to the
    customer's metered subscription item.

    Stripe's metered billing rolls these up into the next invoice ·
    you don't need to charge the card yourself.
    """
    stripe = _stripe()
    record = stripe.SubscriptionItem.create_usage_record(
        subscription_item_id,
        quantity=quantity,
        timestamp=timestamp,
        action=action,
    )
    return dict(record)


def open_customer_portal(*, customer_id: str, return_url: str) -> str:
    """Return a hosted billing portal URL · Stripe handles the entire
    plan-change / payment-method / invoice-download UI.

    This is the "manage subscription" button in your app · sends the
    user to Stripe's portal and back.
    """
    stripe = _stripe()
    sess = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=return_url
    )
    return str(sess.url)
