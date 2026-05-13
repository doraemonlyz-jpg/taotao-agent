"""Tests for the Postgres saver opt-in.

These tests don't actually connect to Postgres (we don't ship a pg
fixture · that's docker-compose territory).  They verify:
  - DATABASE_URL parsing accepts both postgres:// and postgresql://
  - Empty / non-postgres DSN returns None (sqlite fallback)
  - Malformed DSN returns None gracefully
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_db_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield


class TestPostgresDsn:
    def test_unset_returns_none(self):
        from agent.graph import _postgres_dsn

        assert _postgres_dsn() is None

    def test_empty_string_returns_none(self, monkeypatch):
        from agent.graph import _postgres_dsn

        monkeypatch.setenv("DATABASE_URL", "")
        assert _postgres_dsn() is None

    def test_whitespace_only_returns_none(self, monkeypatch):
        from agent.graph import _postgres_dsn

        monkeypatch.setenv("DATABASE_URL", "   ")
        assert _postgres_dsn() is None

    def test_postgres_scheme(self, monkeypatch):
        from agent.graph import _postgres_dsn

        monkeypatch.setenv(
            "DATABASE_URL", "postgres://user:pass@localhost:5432/taotao"
        )
        assert _postgres_dsn() == "postgres://user:pass@localhost:5432/taotao"

    def test_postgresql_scheme(self, monkeypatch):
        from agent.graph import _postgres_dsn

        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://taotao:secret@db:5432/agent"
        )
        assert _postgres_dsn() == "postgresql://taotao:secret@db:5432/agent"

    def test_unsupported_scheme_returns_none(self, monkeypatch):
        from agent.graph import _postgres_dsn

        # We only support postgres at this layer · mysql/sqlite/etc. ignored
        monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost/db")
        assert _postgres_dsn() is None

    def test_sqlite_url_returns_none(self, monkeypatch):
        from agent.graph import _postgres_dsn

        monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/foo.db")
        assert _postgres_dsn() is None
