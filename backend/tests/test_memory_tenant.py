"""Tests for multi-tenant memory isolation.

This is the #1 SaaS data-leak risk · these tests are non-negotiable.
A regression here = customer data leaking across accounts.
"""
from __future__ import annotations

from agent.auth import Identity, use_identity
from agent.memory import get_memory
from agent.memory.long_term import LongTermMemory


class TestPerTenantCollections:
    def test_explicit_tenant_isolated(self):
        """LongTermMemory.for_tenant(X) and for_tenant(Y) get distinct
        physical collections."""
        a = LongTermMemory.for_tenant("alpha")
        b = LongTermMemory.for_tenant("beta")
        assert a.collection.name == "agent_memories_alpha"
        assert b.collection.name == "agent_memories_beta"
        assert a.collection.name != b.collection.name

    def test_default_tenant_when_none(self):
        m = LongTermMemory.for_tenant(None)
        assert m.tenant_id == "default"
        assert m.collection.name == "agent_memories_default"

    def test_unsafe_tenant_id_normalised(self):
        """Path-traversal-shaped tenant IDs get sluggified, not rejected."""
        m = LongTermMemory.for_tenant("../../evil")
        assert m.collection.name == "agent_memories_evil"


class TestContextVarBridgedMemory:
    """get_memory() with no args reads the current ContextVar tenant."""

    def test_no_context_falls_back_to_default(self):
        m = get_memory()
        assert m.tenant_id == "default"

    def test_use_identity_changes_get_memory_tenant(self):
        with use_identity(Identity("u1", "acme")):
            m = get_memory()
            assert m.tenant_id == "acme"

    def test_explicit_tenant_id_overrides_context(self):
        with use_identity(Identity("u1", "acme")):
            m = get_memory(tenant_id="other")
            assert m.tenant_id == "other"


class TestTenantIsolationLeakProof:
    """The actual end-to-end isolation test.  Write to tenant A · read
    from tenant B · MUST not see A's data."""

    def test_no_leak_via_get_memory(self):
        # Write a unique secret in acme's namespace
        with use_identity(Identity("u1", "leakproof-acme")):
            mem_acme = get_memory()
            mem_acme.clear()  # hermetic · fresh state
            mem_acme.remember("acme-secret-fingerprint-99887", kind="fact")

        # Switch to a different tenant · query · MUST be empty
        with use_identity(Identity("u2", "leakproof-zoo")):
            mem_zoo = get_memory()
            mem_zoo.clear()
            # Empty collection at this point
            hits = mem_zoo.collection.query(
                query_texts=["secret"], n_results=10
            )
            docs = (hits.get("documents") or [[]])[0]
            assert "acme-secret-fingerprint-99887" not in docs
            # Even with empty collection, query should not error
            assert isinstance(docs, list)

        # Also verify acme still has its data
        with use_identity(Identity("u1", "leakproof-acme")):
            mem_acme = get_memory()
            hits = mem_acme.collection.query(
                query_texts=["secret"], n_results=10
            )
            docs = (hits.get("documents") or [[]])[0]
            assert "acme-secret-fingerprint-99887" in docs

    def test_no_leak_via_for_tenant(self):
        """Same isolation test using the explicit for_tenant API."""
        a = LongTermMemory.for_tenant("explicit-acme")
        z = LongTermMemory.for_tenant("explicit-zoo")
        a.clear()
        z.clear()

        a.remember("explicit-marker-44556", kind="fact")

        z_hits = z.collection.query(query_texts=["marker"], n_results=10)
        z_docs = (z_hits.get("documents") or [[]])[0]
        assert "explicit-marker-44556" not in z_docs

        a_hits = a.collection.query(query_texts=["marker"], n_results=10)
        a_docs = (a_hits.get("documents") or [[]])[0]
        assert "explicit-marker-44556" in a_docs


class TestMemoryCacheReuse:
    """get_memory(tenant_id=X) should reuse the same instance · cheap
    repeat lookups, no chroma reconnection thrash."""

    def test_same_tenant_returns_same_instance(self):
        a1 = get_memory(tenant_id="cache-test")
        a2 = get_memory(tenant_id="cache-test")
        assert a1 is a2

    def test_different_tenants_different_instances(self):
        a = get_memory(tenant_id="cache-a")
        b = get_memory(tenant_id="cache-b")
        assert a is not b
