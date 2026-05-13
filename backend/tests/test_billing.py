"""Tests for the Stripe billing skeleton.

We don't hit real Stripe (no keys in CI) · we test:
  - Plan catalog shape + plan_for fallback to Free
  - Tenant upsert / get / list (sqlite roundtrip)
  - Webhook event idempotency (same event_id processed once)
  - is_configured() honors env
  - /billing/plans returns the catalog
  - /billing/checkout returns 503 when STRIPE_API_KEY unset
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.billing import PLANS, get_tenant, plan_for, upsert_tenant
from agent.billing.models import record_event
from agent.billing.stripe_client import is_configured


@pytest.fixture(scope="module")
def client():
    from app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_stripe_env(monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_billing_db():
    """Wipe the billing tables before each test · the sqlite file persists
    for the pytest session, so without this `record_event(evt_id_1)` would
    fail on the second test run (PK violation)."""
    from agent.billing.models import _conn

    c = _conn()
    c.execute("DELETE FROM usage_events")
    c.execute("DELETE FROM tenants")
    c.commit()
    yield


# ---------------------------------------------------------------- plans
class TestPlanCatalog:
    def test_all_plans_have_required_fields(self):
        for p in PLANS.values():
            assert p.id
            assert p.name
            assert p.price_usd_per_month >= 0
            assert p.included_tokens >= 0
            assert p.daily_token_cap >= 0

    def test_plan_for_unknown_returns_free(self):
        plan = plan_for("nonexistent-tier")
        assert plan.id == "free"

    def test_plan_for_none_returns_free(self):
        plan = plan_for(None)
        assert plan.id == "free"

    def test_plan_for_known(self):
        plan = plan_for("pro")
        assert plan.id == "pro"


# ---------------------------------------------------------------- tenants
class TestTenantUpsert:
    def test_create_then_read(self):
        t = "test-tenant-create-1"
        row = upsert_tenant(tenant_id=t, plan_id="starter")
        assert row["tenant_id"] == t
        assert row["plan_id"] == "starter"

        again = get_tenant(t)
        assert again is not None
        assert again["plan_id"] == "starter"

    def test_update_only_provided_fields(self):
        t = "test-tenant-update-1"
        upsert_tenant(tenant_id=t, plan_id="starter", stripe_customer_id="cus_abc")
        # Now bump only plan_id · stripe_customer_id should stay
        upsert_tenant(tenant_id=t, plan_id="pro")
        row = get_tenant(t)
        assert row["plan_id"] == "pro"
        assert row["stripe_customer_id"] == "cus_abc"  # preserved!

    def test_default_plan_is_free(self):
        t = "test-tenant-default-1"
        row = upsert_tenant(tenant_id=t)
        assert row["plan_id"] == "free"


# ---------------------------------------------------------------- webhook idempotency
class TestEventIdempotency:
    def test_first_insert_returns_true(self):
        ok = record_event(
            stripe_event_id="evt_test_idem_1",
            event_type="checkout.session.completed",
            tenant_id="t-idem-1",
            payload_json="{}",
        )
        assert ok is True

    def test_duplicate_returns_false(self):
        record_event(
            stripe_event_id="evt_test_idem_2",
            event_type="checkout.session.completed",
            tenant_id="t-idem-2",
            payload_json="{}",
        )
        ok = record_event(
            stripe_event_id="evt_test_idem_2",
            event_type="checkout.session.completed",
            tenant_id="t-idem-2",
            payload_json='{"different": true}',
        )
        assert ok is False


# ---------------------------------------------------------------- stripe client
class TestStripeClientGate:
    def test_unconfigured_when_keys_blank(self):
        assert is_configured() is False

    def test_partial_config_is_unconfigured(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
        # No webhook secret · still not "configured"
        assert is_configured() is False

    def test_full_config(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
        assert is_configured() is True


# ---------------------------------------------------------------- HTTP routes
class TestBillingRoutes:
    def test_plans_endpoint(self, client):
        r = client.get("/billing/plans")
        assert r.status_code == 200
        body = r.json()
        ids = {p["id"] for p in body}
        assert "free" in ids
        assert "pro" in ids

    def test_me_endpoint_returns_free_default(self, client):
        r = client.get("/billing/me")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["plan"]["id"] == "free"
        assert body["stripe"]["configured"] is False

    def test_checkout_503_when_unconfigured(self, client):
        r = client.post(
            "/billing/checkout",
            json={
                "plan_id": "pro",
                "success_url": "https://app/ok",
                "cancel_url": "https://app/cancel",
            },
        )
        assert r.status_code == 503, r.text

    def test_webhook_503_when_unconfigured(self, client):
        r = client.post("/billing/webhook", content=b"{}")
        assert r.status_code == 503, r.text

    def test_billing_paths_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = set(spec["paths"].keys())
        for p in (
            "/billing/plans",
            "/billing/me",
            "/billing/checkout",
            "/billing/portal",
            "/billing/webhook",
        ):
            assert p in paths
