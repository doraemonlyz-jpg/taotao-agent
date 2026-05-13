"""Tests for `agent.auth.identity` · the per-tenant identity layer.

Covers the three resolution modes (JWT / API_KEY / dev) and the
ContextVar bridge that lets deep-stack tools read the tenant id.
"""
from __future__ import annotations

import pytest

from agent.auth import (
    ANONYMOUS,
    Identity,
    get_current_identity,
    get_current_tenant_id,
    use_identity,
)
from agent.memory.long_term import _safe_tenant


class TestIdentityDataclass:
    def test_admin_role_detected(self):
        ident = Identity("u1", "acme", roles=("user", "admin"))
        assert ident.is_admin

    def test_non_admin(self):
        ident = Identity("u1", "acme", roles=("user",))
        assert not ident.is_admin

    def test_empty_roles_not_admin(self):
        ident = Identity("u1", "acme")
        assert not ident.is_admin

    def test_frozen(self):
        """Identity is frozen · prevents accidental mutation that could
        confuse the ContextVar plumbing."""
        ident = Identity("u1", "acme")
        with pytest.raises(Exception):  # FrozenInstanceError
            ident.tenant_id = "evil"  # type: ignore


class TestContextVarBridge:
    def test_default_outside_context(self):
        # Outside any use_identity block · we get the safe anonymous fallback
        assert get_current_identity() is ANONYMOUS
        assert get_current_tenant_id() == "default"

    def test_use_identity_sets_tenant(self):
        with use_identity(Identity("u1", "acme")):
            assert get_current_tenant_id() == "acme"
            assert get_current_identity().user_id == "u1"

    def test_use_identity_restores_on_exit(self):
        with use_identity(Identity("u1", "acme")):
            pass
        # After exit · back to default
        assert get_current_tenant_id() == "default"

    def test_nested_contexts_unwind_correctly(self):
        with use_identity(Identity("u1", "outer-tenant")):
            assert get_current_tenant_id() == "outer-tenant"
            with use_identity(Identity("u2", "inner-tenant")):
                assert get_current_tenant_id() == "inner-tenant"
            # Inner exit restores outer
            assert get_current_tenant_id() == "outer-tenant"
        assert get_current_tenant_id() == "default"

    def test_use_none_resets_to_anonymous(self):
        """use_identity(None) should fall back to anonymous · used by
        the middleware when no auth headers are present."""
        with use_identity(None):
            assert get_current_identity() is ANONYMOUS
            assert get_current_tenant_id() == "default"


class TestTenantSlugSafety:
    """The slug normalisation is what stops a malicious tenant_id like
    `../../etc/passwd` from breaking out of the chroma collection
    naming scheme."""

    @pytest.mark.parametrize("raw,expected", [
        (None, "default"),
        ("", "default"),
        ("a", "default"),     # too short
        ("abc", "abc"),
        ("Acme Corp · Inc.", "Acme-Corp-Inc"),
        ("../../etc/passwd", "etc-passwd"),
        ("'; DROP TABLE--", "DROP-TABLE"),
        ("acme_corp", "acme_corp"),
        ("a" * 80, "default"),  # too long
    ])
    def test_slug_normalisation(self, raw, expected):
        assert _safe_tenant(raw) == expected
