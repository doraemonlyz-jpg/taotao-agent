"""Structured logging setup · JSON or pretty, env-gated.

Two output modes:

  - LOG_FORMAT=json (default in production / Docker)
      One line of JSON per log record · easy to ship to Datadog,
      Honeycomb, Loki, ELK, etc.  Fields:
        ts (ISO-8601), level, logger, msg, plus any `extra={}` dict
        attached by the caller, plus exception info if raised.

  - LOG_FORMAT=pretty (default for `python app.py` from a TTY)
      Human-readable colorized format.  No timestamps because most
      dev shells already prepend their own.

The current request's tenant + user (set by the identity middleware)
is injected automatically into every record · so `log.info("memory cleared")`
inside a request handler shows up as
`{"tenant": "acme-corp", "user": "alice@acme", "msg": "memory cleared", ...}`
without the caller having to plumb it.

Idempotent · safe to call multiple times (e.g. uvicorn reload).

Why standard `logging` instead of structlog?
  - Zero new dependency · stdlib already does JSON via `logging.Formatter`.
  - structlog is excellent but adds a learning curve for contributors.
  - All third-party libs (langgraph, fastapi, sse-starlette) emit via
    stdlib logging, so we get consistent formatting "for free".
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Field names we never want to leak from the LogRecord into the JSON
# output · these are stdlib internals.
_BUILTIN_RECORD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


_INITIALISED = False


def _level_from_env() -> int:
    """`LOG_LEVEL=debug|info|warning|error` → logging.DEBUG/INFO/...
    Defaults to INFO."""
    name = (os.environ.get("LOG_LEVEL") or "info").upper()
    return getattr(logging, name, logging.INFO)


def _format_from_env() -> str:
    """`LOG_FORMAT=json|pretty`. Defaults to json when running under
    Docker / non-TTY, pretty when running interactively."""
    explicit = (os.environ.get("LOG_FORMAT") or "").strip().lower()
    if explicit in {"json", "pretty"}:
        return explicit
    return "pretty" if sys.stderr.isatty() else "json"


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record · default.

    Custom `extra={"k": v}` keys are merged into the top level. The
    request-scoped tenant/user identity is pulled from the auth
    ContextVar so structured logs are immediately searchable by tenant
    in Datadog / Honeycomb / Grafana Loki / etc.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Inject request-scoped tenant/user when available.  Safe to
        # import here because identity has no heavy deps.
        try:
            from agent.auth.identity import get_current_identity  # noqa: PLC0415

            ident = get_current_identity()
            payload["tenant"] = ident.tenant_id
            payload["user"] = ident.user_id
        except Exception:
            # During import time / tests / before middleware runs.
            pass

        # Caller-attached `extra={...}` dict ends up as record attrs.
        # Merge anything that isn't a stdlib LogRecord field.
        for key, val in record.__dict__.items():
            if key in _BUILTIN_RECORD_KEYS or key in payload:
                continue
            try:
                json.dumps(val)  # JSON-roundtrip check
                payload[key] = val
            except Exception:
                payload[key] = repr(val)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info

        return json.dumps(payload, ensure_ascii=False, default=str)


class _PrettyFormatter(logging.Formatter):
    """Colorized one-line format for interactive dev.

    Uses ANSI escapes when stderr is a TTY · falls back to plain text
    otherwise (which makes `pytest -s` and CI logs readable).
    """

    _COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO":  "\033[32m",     # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[35m",  # magenta
    }
    _RESET = "\033[0m"

    def __init__(self) -> None:
        super().__init__()
        self._use_color = sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        if self._use_color:
            level_fmt = f"{self._COLORS.get(level, '')}{level:<7}{self._RESET}"
        else:
            level_fmt = f"{level:<7}"
        head = f"{level_fmt} {record.name:<22}"
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return f"{head} | {msg}"


def configure_logging(*, level: int | None = None, fmt: str | None = None) -> None:
    """Install JSON or pretty handler on the ROOT logger.

    Call once at application startup.  Subsequent calls are no-ops so
    uvicorn reload doesn't double-log.

    Pass explicit `level` / `fmt` to override env detection (useful in
    tests).  Otherwise reads `LOG_LEVEL` and `LOG_FORMAT` from env.
    """
    global _INITIALISED
    if _INITIALISED:
        return
    _INITIALISED = True

    eff_level = level if level is not None else _level_from_env()
    eff_fmt = (fmt or _format_from_env()).lower()

    handler = logging.StreamHandler(sys.stderr)
    if eff_fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_PrettyFormatter())

    root = logging.getLogger()
    # Replace existing handlers · uvicorn / langgraph install their own
    # which would cause double-emit. Keeping our single handler.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(eff_level)

    # Quiet down noisy libraries that log INFO for every operation.
    # They still emit ERROR / WARNING which we care about.
    for noisy in ("httpx", "httpcore", "openai", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
