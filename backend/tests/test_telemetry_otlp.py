"""Unit tests for OTLP exporter env parsing.

These tests don't actually start the OTel SDK · they just verify that
the env-var → exporter-config translation is correct.  The SDK lifecycle
is tested by the smoke-test in test_app.py (the test client app boot
calls `install_telemetry`).

Critical behaviors locked here:
  - OTLP headers parse as comma-separated `key=value`
  - Empty / malformed env returns None
  - Insecure auto-detection: gRPC localhost = plain, HTTPS = TLS
  - Honeycomb config (HTTP + headers) round-trips correctly
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_otlp_env(monkeypatch):
    """Clear all OTLP env vars before each test for hermetic isolation."""
    for k in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_INSECURE",
        "OTEL_DEPLOYMENT_ENVIRONMENT",
        "OTEL_SERVICE_NAME",
        "ENV",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


class TestProtocol:
    def test_default_grpc(self):
        from agent.observability.telemetry import _otlp_protocol

        assert _otlp_protocol() == "grpc"

    def test_explicit_http(self, monkeypatch):
        from agent.observability.telemetry import _otlp_protocol

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        assert _otlp_protocol() == "http/protobuf"

    def test_case_insensitive(self, monkeypatch):
        from agent.observability.telemetry import _otlp_protocol

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "GRPC")
        assert _otlp_protocol() == "grpc"


class TestHeaders:
    def test_empty_returns_none(self):
        from agent.observability.telemetry import _otlp_headers

        assert _otlp_headers() is None

    def test_single_pair(self, monkeypatch):
        from agent.observability.telemetry import _otlp_headers

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-honeycomb-team=secret123")
        assert _otlp_headers() == {"x-honeycomb-team": "secret123"}

    def test_multiple_pairs(self, monkeypatch):
        from agent.observability.telemetry import _otlp_headers

        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_HEADERS",
            "x-api-key=abc, x-dataset=prod, Authorization=Bearer xyz",
        )
        h = _otlp_headers()
        assert h == {
            "x-api-key": "abc",
            "x-dataset": "prod",
            "Authorization": "Bearer xyz",
        }

    def test_malformed_pairs_skipped(self, monkeypatch):
        from agent.observability.telemetry import _otlp_headers

        # Missing `=` · should be silently skipped
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "valid=ok,broken,also=fine")
        assert _otlp_headers() == {"valid": "ok", "also": "fine"}


class TestInsecure:
    def test_default_grpc_localhost_is_insecure(self, monkeypatch):
        from agent.observability.telemetry import _otlp_insecure

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        # protocol defaults to grpc · localhost · → insecure
        assert _otlp_insecure() is True

    def test_default_https_endpoint_is_secure(self, monkeypatch):
        from agent.observability.telemetry import _otlp_insecure

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://api.honeycomb.io/v1/traces")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        assert _otlp_insecure() is False

    def test_explicit_insecure_overrides(self, monkeypatch):
        from agent.observability.telemetry import _otlp_insecure

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.com")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "1")
        assert _otlp_insecure() is True

    def test_explicit_secure_overrides(self, monkeypatch):
        from agent.observability.telemetry import _otlp_insecure

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "0")
        assert _otlp_insecure() is False


class TestServiceEnv:
    def test_default_dev(self):
        from agent.observability.telemetry import _service_env

        assert _service_env() == "dev"

    def test_otel_var_wins(self, monkeypatch):
        from agent.observability.telemetry import _service_env

        monkeypatch.setenv("OTEL_DEPLOYMENT_ENVIRONMENT", "prod")
        monkeypatch.setenv("ENV", "staging")
        assert _service_env() == "prod"

    def test_env_fallback(self, monkeypatch):
        from agent.observability.telemetry import _service_env

        monkeypatch.setenv("ENV", "staging")
        assert _service_env() == "staging"


class TestHoneycombScenario:
    """End-to-end: simulate a Honeycomb config and verify all helpers
    return the right thing.  This is the most likely real-world setup."""

    def test_full_honeycomb_config(self, monkeypatch):
        from agent.observability.telemetry import (
            _otlp_endpoint,
            _otlp_headers,
            _otlp_insecure,
            _otlp_protocol,
        )

        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "https://api.honeycomb.io/v1/traces"
        )
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_HEADERS",
            "x-honeycomb-team=hcaik_super_secret,x-honeycomb-dataset=taotao-prod",
        )

        assert _otlp_endpoint() == "https://api.honeycomb.io/v1/traces"
        assert _otlp_protocol() == "http/protobuf"
        assert _otlp_headers() == {
            "x-honeycomb-team": "hcaik_super_secret",
            "x-honeycomb-dataset": "taotao-prod",
        }
        # HTTPS endpoint · auto-secure
        assert _otlp_insecure() is False
