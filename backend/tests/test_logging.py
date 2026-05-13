"""Tests for the structured logging setup.

Why these matter: structured logs are how we'd debug production · a
broken JSON formatter would mean every "I don't know what's wrong" hour
is spent grepping malformed text. We pin:

  - JSON output is valid JSON
  - tenant + user from identity ContextVar are injected automatically
  - extra={} fields land in the JSON top-level
  - exception info is captured
  - configure_logging() is idempotent (safe across uvicorn reload)
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from agent.auth.identity import Identity, use_identity
from agent.observability.logging_setup import (
    _JsonFormatter,
    _PrettyFormatter,
    configure_logging,
)


def _capture(formatter: logging.Formatter, level: int = logging.INFO) -> tuple[logging.Logger, io.StringIO]:
    """Build a fresh logger writing into a StringIO buffer.

    We can't use root because configure_logging() owns it · use a
    detached test logger instead.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(formatter)
    log = logging.Logger(name="test.logger", level=level)
    log.addHandler(handler)
    return log, buf


class TestJsonFormatter:
    def test_basic_record_is_valid_json(self):
        log, buf = _capture(_JsonFormatter())
        log.info("hello world")
        line = buf.getvalue().strip()
        obj = json.loads(line)
        assert obj["msg"] == "hello world"
        assert obj["level"] == "INFO"
        assert obj["logger"] == "test.logger"
        assert "ts" in obj

    def test_extra_fields_merged_into_top_level(self):
        log, buf = _capture(_JsonFormatter())
        log.info("memory cleared", extra={"count": 42, "kind": "facts"})
        obj = json.loads(buf.getvalue().strip())
        assert obj["count"] == 42
        assert obj["kind"] == "facts"

    def test_unserializable_extra_falls_back_to_repr(self):
        class Weird:
            def __repr__(self) -> str:
                return "<Weird>"

        log, buf = _capture(_JsonFormatter())
        log.info("got widget", extra={"widget": Weird()})
        obj = json.loads(buf.getvalue().strip())
        assert obj["widget"] == "<Weird>"

    def test_exception_info_captured(self):
        log, buf = _capture(_JsonFormatter())
        try:
            raise ValueError("bang")
        except ValueError:
            log.exception("boom")
        obj = json.loads(buf.getvalue().strip())
        assert "ValueError: bang" in obj["exc"]

    def test_identity_contextvar_injected_when_set(self):
        log, buf = _capture(_JsonFormatter())
        ident = Identity(user_id="alice", tenant_id="acme", email=None, roles=("user",))
        with use_identity(ident):
            log.info("inside request")
        obj = json.loads(buf.getvalue().strip())
        assert obj["tenant"] == "acme"
        assert obj["user"] == "alice"

    def test_identity_omitted_when_not_set(self):
        log, buf = _capture(_JsonFormatter())
        # No `with use_identity(...)` · ContextVar default is anonymous
        log.info("background job")
        obj = json.loads(buf.getvalue().strip())
        # ANONYMOUS sentinel still resolves · defaults to "default" tenant.
        assert obj.get("tenant") == "default"
        assert obj.get("user") == "anonymous"


class TestPrettyFormatter:
    def test_renders_one_line(self):
        log, buf = _capture(_PrettyFormatter())
        log.warning("oh no")
        line = buf.getvalue()
        # Should be a single line (no embedded newlines from formatter).
        assert line.count("\n") == 1
        assert "WARNING" in line
        assert "oh no" in line


class TestConfigureLogging:
    def test_idempotent(self):
        # Multiple calls should not stack handlers (uvicorn reload case).
        configure_logging(level=logging.INFO, fmt="json")
        n1 = len(logging.getLogger().handlers)
        configure_logging(level=logging.DEBUG, fmt="pretty")  # ignored second time
        n2 = len(logging.getLogger().handlers)
        assert n1 == n2

    def test_quiets_noisy_libs(self):
        configure_logging()
        for noisy in ("httpx", "httpcore", "openai", "anthropic", "urllib3"):
            assert logging.getLogger(noisy).level >= logging.WARNING


@pytest.fixture(autouse=True)
def _reset_logging():
    """Tests in this module mutate the root logger.  Restore before each."""
    yield
    # Don't actually reset · configure_logging is intentionally
    # idempotent and other tests in the suite may depend on its state.
    # No-op cleanup.
