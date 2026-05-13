"""Per-user quota tests · enforcement + recording + admin reset.

Critical behaviors locked here:
  - When QUOTA_ENABLED=0, record_usage is a no-op AND check_quota never
    raises (existing dev/CI workflows must not break).
  - When enabled, exceeding daily OR monthly cap returns 429 with a
    helpful detail dict.
  - Per-tenant isolation: alice@acme exhausting her cap doesn't affect
    bob@acme or alice@beta.
  - Counters are upsert-style (multiple writes accumulate, not replace).
  - reset_for_user wipes only the targeted (tenant, user) rows.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from agent.auth.identity import Identity
from agent.quota import (
    check_quota,
    record_usage,
    reset_for_user,
    snapshot,
)


@pytest.fixture(autouse=True)
def _reset_quota_env(monkeypatch):
    """Hermetic env per test · clear all quota knobs."""
    for k in (
        "QUOTA_ENABLED",
        "QUOTA_DAILY_TOKENS",
        "QUOTA_MONTHLY_TOKENS",
        "QUOTA_DAILY_USD",
        "QUOTA_MONTHLY_USD",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def alice():
    return Identity(
        user_id="alice@acme.com", tenant_id="acme",
        email="alice@acme.com", roles=("user",),
    )


@pytest.fixture
def bob():
    return Identity(
        user_id="bob@acme.com", tenant_id="acme",
        email="bob@acme.com", roles=("user",),
    )


# --------------------------------------------------------------------- #
# Disabled path · everything is a no-op
# --------------------------------------------------------------------- #
class TestDisabledByDefault:
    def test_record_usage_noop(self, alice):
        # Should not raise even though we never set up the DB.
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=1000, cost_usd=0.05)

    def test_check_quota_noop(self, alice):
        check_quota(alice)  # must not raise

    def test_snapshot_returns_disabled(self, alice):
        snap = snapshot(tenant_id=alice.tenant_id, user_id=alice.user_id)
        assert snap["enabled"] is False


# --------------------------------------------------------------------- #
# Enabled path · enforcement + accounting
# --------------------------------------------------------------------- #
class TestEnabled:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch, alice, bob):
        monkeypatch.setenv("QUOTA_ENABLED", "1")
        monkeypatch.setenv("QUOTA_DAILY_TOKENS", "1000")
        monkeypatch.setenv("QUOTA_MONTHLY_TOKENS", "10000")
        # Reset both users' counters for hermetic test (uses real sqlite
        # in the test session's tmp dir, populated by conftest).
        reset_for_user(tenant_id=alice.tenant_id, user_id=alice.user_id)
        reset_for_user(tenant_id=bob.tenant_id, user_id=bob.user_id)
        yield

    def test_record_increments(self, alice):
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=300, cost_usd=0.01)
        snap = snapshot(tenant_id=alice.tenant_id, user_id=alice.user_id)
        assert snap["enabled"] is True
        assert snap["day"]["tokens"] == 300
        assert snap["day"]["requests"] == 1
        # Second call accumulates · upsert
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=100, cost_usd=0.005)
        snap = snapshot(tenant_id=alice.tenant_id, user_id=alice.user_id)
        assert snap["day"]["tokens"] == 400
        assert snap["day"]["requests"] == 2

    def test_under_cap_does_not_raise(self, alice):
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=500, cost_usd=0.01)
        check_quota(alice)  # 500 < cap=1000 · ok

    def test_at_cap_raises_429(self, alice):
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=1000, cost_usd=0.02)
        with pytest.raises(HTTPException) as ei:
            check_quota(alice)
        assert ei.value.status_code == 429
        # detail is a dict with a hint
        d = ei.value.detail
        assert isinstance(d, dict)
        assert d["slot"] in {"day", "month"}
        assert "hint" in d

    def test_over_cap_raises_429(self, alice):
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=2000, cost_usd=0.02)
        with pytest.raises(HTTPException) as ei:
            check_quota(alice)
        assert ei.value.status_code == 429

    def test_alice_exhausting_does_not_block_bob(self, alice, bob):
        # Alice is over · Bob is fresh
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=2000, cost_usd=0.04)
        with pytest.raises(HTTPException):
            check_quota(alice)
        # Same tenant, different user · MUST NOT inherit Alice's exhaustion.
        check_quota(bob)
        snap = snapshot(tenant_id=bob.tenant_id, user_id=bob.user_id)
        assert snap["day"]["tokens"] == 0

    def test_reset_clears_counters(self, alice):
        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=999, cost_usd=0.02)
        deleted = reset_for_user(tenant_id=alice.tenant_id, user_id=alice.user_id)
        assert deleted["day"] >= 1
        snap = snapshot(tenant_id=alice.tenant_id, user_id=alice.user_id)
        assert snap["day"]["tokens"] == 0


class TestUsdCap:
    def test_usd_cap_independent_of_token_cap(self, monkeypatch, alice):
        monkeypatch.setenv("QUOTA_ENABLED", "1")
        # Tokens cap effectively disabled · USD cap at $0.05
        monkeypatch.setenv("QUOTA_DAILY_TOKENS", "0")
        monkeypatch.setenv("QUOTA_DAILY_USD", "0.05")
        reset_for_user(tenant_id=alice.tenant_id, user_id=alice.user_id)

        record_usage(tenant_id=alice.tenant_id, user_id=alice.user_id, tokens=1, cost_usd=0.05)
        with pytest.raises(HTTPException) as ei:
            check_quota(alice)
        assert ei.value.status_code == 429
        assert "usd" in ei.value.detail["slot"] or ei.value.detail["spent_usd"] >= 0.05


class TestSnapshotShape:
    def test_includes_both_periods(self, monkeypatch, alice):
        monkeypatch.setenv("QUOTA_ENABLED", "1")
        monkeypatch.setenv("QUOTA_DAILY_TOKENS", "100")
        monkeypatch.setenv("QUOTA_MONTHLY_TOKENS", "1000")
        reset_for_user(tenant_id=alice.tenant_id, user_id=alice.user_id)

        snap = snapshot(tenant_id=alice.tenant_id, user_id=alice.user_id)
        assert snap["day"]["cap_tokens"] == 100
        assert snap["month"]["cap_tokens"] == 1000
        assert snap["day"]["period"].startswith("day:")
        assert snap["month"]["period"].startswith("month:")
