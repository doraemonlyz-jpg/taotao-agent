"""Billing module · Stripe metered billing skeleton.

What's included (skeleton-grade · ready to wire to a real Stripe account):

  - `plans.py`        · plan catalog (free / starter / pro / enterprise) +
                        per-plan quota lookup
  - `stripe_client.py`· thin wrapper around stripe-python · usage_record
                        push, customer create, checkout session start
  - `webhook.py`      · /billing/webhook handler · invoice.paid /
                        subscription.updated / customer.subscription.deleted
  - `models.py`       · sqlite schema for `tenants` (current plan +
                        stripe_customer_id) and `usage_events` (audit log)

What's NOT included (intentional · scope = "skeleton"):

  - Real Stripe account · use Stripe CLI to spin up test mode
    (`stripe listen --forward-to localhost:8000/billing/webhook`)
  - Tax/VAT compute · use Stripe Tax in production
  - Proration · Stripe handles this server-side when you switch plans
  - Dunning emails · use Stripe's built-in dunning + Customer Portal
  - Invoice PDF rendering · Stripe gives you hosted ones for free

Use this module as the integration target for `stripe listen` and the
contract surface for your billing UI.  Do NOT rely on it as a
production billing system unsupervised · billing bugs are existential.
"""
from .models import (
    ensure_schema,
    get_tenant,
    upsert_tenant,
)
from .plans import PLANS, Plan, plan_for

__all__ = [
    "PLANS",
    "Plan",
    "plan_for",
    "ensure_schema",
    "get_tenant",
    "upsert_tenant",
]
