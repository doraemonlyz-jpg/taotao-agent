"""
OpenTelemetry + Prometheus wiring · env-gated.

Two layers of observability are added by `install_telemetry(app)`:

  1. Distributed tracing (OpenTelemetry)
       - Auto-instruments every FastAPI route + every httpx call.
       - Provides a `tool_span()` helper for hand-instrumented tools and
         a `subagent_span()` helper for sub-agent dispatch.
       - Exporter:
           - OTEL_EXPORTER_OTLP_ENDPOINT set → OTLP gRPC (Jaeger / Tempo /
             Datadog Agent / etc.)
           - unset → ConsoleSpanExporter (handy for `tail -f` debugging)

  2. RED metrics (Prometheus)
       - GET /metrics is added to the FastAPI app
       - Default histograms: http_requests_total, request_duration_seconds,
         broken down by route + status code.

All env vars are optional · this module is a pure no-op when nothing is
configured but never hides failures (we log warnings).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI

log = logging.getLogger("agent.telemetry")

# --------------------------------------------------------------- state
_INITIALISED = False
_TRACER = None  # type: ignore[var-annotated]


def _otlp_endpoint() -> str | None:
    v = (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    return v or None


def _service_name() -> str:
    return os.environ.get("OTEL_SERVICE_NAME") or "taotao-agent"


# --------------------------------------------------------------- init
def install_telemetry(app: FastAPI) -> dict:
    """Wire OTel + Prometheus into the FastAPI app. Idempotent."""
    global _INITIALISED, _TRACER
    status = {"otel": False, "otel_exporter": "none", "prometheus": False}

    if _INITIALISED:
        return status

    # ----- OTel core ------------------------------------------------
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        resource = Resource.create({
            "service.name": _service_name(),
            "service.version": "0.2.0",
        })
        provider = TracerProvider(resource=resource)

        endpoint = _otlp_endpoint()
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
                status["otel_exporter"] = f"otlp:{endpoint}"
            except Exception as e:  # pragma: no cover
                log.warning("OTLP exporter unavailable, falling back to console: %s", e)
                exporter = ConsoleSpanExporter()
                status["otel_exporter"] = "console (OTLP unavailable)"
        else:
            exporter = ConsoleSpanExporter()
            status["otel_exporter"] = "console"

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("agent")

        # Auto-instrument FastAPI + httpx · zero hand-coding gets us:
        # request_id propagation, latency, status code, route attrs.
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/health")
        HTTPXClientInstrumentor().instrument()

        status["otel"] = True
        log.info("OpenTelemetry initialised · exporter=%s", status["otel_exporter"])
    except Exception as e:  # pragma: no cover
        log.warning("OpenTelemetry init failed: %s", e)

    # ----- Prometheus ----------------------------------------------
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            excluded_handlers=["/metrics", "/health"],
        ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

        status["prometheus"] = True
        log.info("Prometheus /metrics exposed")
    except Exception as e:  # pragma: no cover
        log.warning("Prometheus init failed: %s", e)

    _INITIALISED = True
    return status


# --------------------------------------------------------------- helpers
@contextmanager
def tool_span(name: str, **attrs) -> Iterator[None]:
    """Wrap a tool call in an OTel span.

    Usage in any tool:

        from agent.observability.telemetry import tool_span
        with tool_span("calculator", expr=expr):
            return numexpr.evaluate(expr)

    Falls back to a no-op contextmanager when OTel isn't initialised
    so tools don't have to know about telemetry.
    """
    if _TRACER is None:
        yield
        return
    with _TRACER.start_as_current_span(f"tool.{name}") as span:
        for k, v in attrs.items():
            try:
                span.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
            except Exception:
                pass
        yield


@contextmanager
def subagent_span(role: str, task: str) -> Iterator[None]:
    """Wrap a sub-agent dispatch in an OTel span (researcher / coder / writer)."""
    if _TRACER is None:
        yield
        return
    with _TRACER.start_as_current_span(f"subagent.{role}") as span:
        span.set_attribute("subagent.role", role)
        span.set_attribute("subagent.task", task[:240])
        yield


@contextmanager
def llm_span(model: str, node: str) -> Iterator[None]:
    """Wrap a single LLM invocation. We only attach high-cardinality-safe
    attributes (model id + node name); token counts are emitted by the
    UsageCallback into the existing event_bus + Prometheus."""
    if _TRACER is None:
        yield
        return
    with _TRACER.start_as_current_span(f"llm.{node}") as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.node", node)
        yield
