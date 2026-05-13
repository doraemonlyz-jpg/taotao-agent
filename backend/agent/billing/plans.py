"""Plan catalog · the source of truth for what each tier gets.

In production you'd typically fetch plan data from Stripe at runtime
(via stripe.Product.list / stripe.Price.list) so marketing can edit
copy + price without a deploy.  For the skeleton we hard-code so the
tests run hermetically.

Wire to Stripe by adding `stripe_price_id` to each Plan and using it
in checkout session creation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    """One subscription tier · maps to a Stripe Price ID in production.

    Attributes:
      id:                   short slug · stable, used as DB foreign key
      name:                 marketing-friendly label
      price_usd_per_month:  flat monthly fee (0 = free)
      included_tokens:      tokens included in the flat fee · per-month
      overage_per_1m_usd:   USD per 1M tokens above included
      daily_token_cap:      hard daily cap (0 = unlimited within plan)
      stripe_price_id:      Stripe Price ID (`price_xxx`) · None when free
    """

    id: str
    name: str
    price_usd_per_month: float
    included_tokens: int
    overage_per_1m_usd: float
    daily_token_cap: int
    stripe_price_id: str | None = None


# Adjust these to taste · keep `id` stable once you have customers.
PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        price_usd_per_month=0.0,
        included_tokens=50_000,
        overage_per_1m_usd=0.0,        # No overage on free · hard cap.
        daily_token_cap=10_000,
        stripe_price_id=None,
    ),
    "starter": Plan(
        id="starter",
        name="Starter",
        price_usd_per_month=29.0,
        included_tokens=2_000_000,
        overage_per_1m_usd=10.0,
        daily_token_cap=200_000,
        stripe_price_id=None,  # set in env: STRIPE_PRICE_STARTER=price_xxx
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        price_usd_per_month=99.0,
        included_tokens=10_000_000,
        overage_per_1m_usd=8.0,
        daily_token_cap=1_000_000,
        stripe_price_id=None,
    ),
    "enterprise": Plan(
        id="enterprise",
        name="Enterprise",
        price_usd_per_month=999.0,
        included_tokens=100_000_000,
        overage_per_1m_usd=5.0,
        daily_token_cap=0,  # unlimited daily · billed by overage
        stripe_price_id=None,
    ),
}


DEFAULT_PLAN_ID = "free"


def plan_for(plan_id: str | None) -> Plan:
    """Return the Plan for `plan_id` · falls back to Free for unknown ids.

    Defensive default: if a tenant somehow has a stale plan_id (e.g.
    we deprecated a plan), they keep working at Free tier instead of
    being locked out.  Log a warning at the call site if you care.
    """
    if not plan_id:
        return PLANS[DEFAULT_PLAN_ID]
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN_ID])
