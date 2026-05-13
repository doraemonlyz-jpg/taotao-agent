"""Tests for the /admin/* RBAC + endpoints.

Pinned behaviors:
  - /admin/* return 403 for non-admin identities (default in tests)
  - ADMIN_USERS env can promote a user_id to admin
  - DSR requires explicit `confirm` matching tenant_id
  - DSR dry_run=True returns preview without deleting
  - cache/clear is idempotent
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.auth.identity import Identity, _admin_user_ids


@pytest.fixture(scope="module")
def client():
    from app import app

    with TestClient(app) as c:
        yield c


class TestAdminEnvParsing:
    def test_empty_admin_users(self, monkeypatch):
        monkeypatch.delenv("ADMIN_USERS", raising=False)
        assert _admin_user_ids() == set()

    def test_single_admin(self, monkeypatch):
        monkeypatch.setenv("ADMIN_USERS", "alice@acme.com")
        assert _admin_user_ids() == {"alice@acme.com"}

    def test_multiple_admins(self, monkeypatch):
        monkeypatch.setenv("ADMIN_USERS", " alice@acme.com , bob@acme.com,ops")
        assert _admin_user_ids() == {"alice@acme.com", "bob@acme.com", "ops"}

    def test_blank_entries_filtered(self, monkeypatch):
        monkeypatch.setenv("ADMIN_USERS", "alice,,bob,")
        assert _admin_user_ids() == {"alice", "bob"}


class TestAdminRouterRegistered:
    def test_admin_paths_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        paths = set(spec["paths"].keys())
        for p in (
            "/admin/me",
            "/admin/tenants",
            "/admin/cache/clear",
            "/admin/dsr",
            "/admin/usage",
            "/admin/disk-usage",
        ):
            assert p in paths, f"{p} missing from OpenAPI"


class TestAdminGateBlocksNonAdmin:
    """In dev mode, current_identity returns roles=("user",) · NOT admin.
    Without ADMIN_USERS bootstrap, every /admin/* should 403."""

    def test_admin_me_403(self, client, monkeypatch):
        monkeypatch.delenv("ADMIN_USERS", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        r = client.get("/admin/me")
        assert r.status_code == 403, r.text

    def test_admin_tenants_403(self, client, monkeypatch):
        monkeypatch.delenv("ADMIN_USERS", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        r = client.get("/admin/tenants")
        assert r.status_code == 403


class TestAdminBootstrapPromotion:
    """ADMIN_USERS env should promote a user_id to admin even if their
    auth payload has no admin role.  This is the bootstrap path · the
    only way to flip the very first admin in a fresh deployment."""

    def test_anonymous_promoted_when_listed(self, client, monkeypatch):
        # In dev mode, current_identity returns user_id="anonymous"
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.setenv("ADMIN_USERS", "anonymous")
        r = client.get("/admin/me")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_admin"] is True
        assert "admin" in body["roles"]


class TestAdminDSR:
    """The most dangerous endpoint · we triple-check the guards."""

    def test_dry_run_returns_preview(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_USERS", "anonymous")
        # Ensure target tenant has at least one memory so .count() > -1.
        client.post("/memory", json={"text": "remember me", "kind": "fact"})
        r = client.post(
            "/admin/dsr",
            json={"tenant_id": "default", "dry_run": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dry_run"] is True
        assert "would_delete" in body
        assert isinstance(body["would_delete"]["memories"], int)

    def test_real_delete_requires_confirm(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_USERS", "anonymous")
        # confirm field MUST equal tenant_id exactly.
        r = client.post(
            "/admin/dsr",
            json={"tenant_id": "default", "dry_run": False, "confirm": "wrong"},
        )
        assert r.status_code == 400, r.text
        assert "confirm" in r.text.lower()

    def test_unknown_tenant_404(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_USERS", "anonymous")
        # `_safe_tenant` always coerces to a slug · we use a clearly empty
        # tenant slug · this still creates a fresh empty collection so
        # we only assert that it doesn't 500.
        r = client.post(
            "/admin/dsr",
            json={"tenant_id": "totally-fresh-tenant-xyz", "dry_run": True},
        )
        # Either 200 (fresh empty collection materialized · count=0)
        # or 404 (chroma rejected · either is sane behavior)
        assert r.status_code in (200, 404), r.text


class TestAdminCacheClear:
    def test_cache_clear_idempotent(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_USERS", "anonymous")
        r1 = client.post("/admin/cache/clear")
        r2 = client.post("/admin/cache/clear")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["ok"] is True
        assert r2.json()["ok"] is True


class TestIdentityIsAdmin:
    """Unit test the Identity.is_admin property + role manipulation."""

    def test_user_role_not_admin(self):
        i = Identity(user_id="u", tenant_id="t", email=None, roles=("user",))
        assert i.is_admin is False

    def test_admin_role_is_admin(self):
        i = Identity(user_id="u", tenant_id="t", email=None, roles=("user", "admin"))
        assert i.is_admin is True

    def test_no_roles_not_admin(self):
        i = Identity(user_id="u", tenant_id="t", email=None, roles=())
        assert i.is_admin is False
