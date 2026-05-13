"""Pytest configuration · shared fixtures.

We deliberately point chroma + sqlite at a temp dir per pytest session so
tests are hermetic (don't pollute the real `data/` directory).  Also
silences telemetry/Sentry by clearing their env vars before any imports.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# --------------------------------------------------------------------- #
# Quiet third-parties · MUST happen before any agent.* import below
# --------------------------------------------------------------------- #
os.environ.pop("SENTRY_DSN", None)
os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ.setdefault("API_KEY", "")  # disable auth in tests by default
# Disable MCP HTTP server in tests · its FastMCP session manager raises
# RuntimeError when a second TestClient enters the lifespan (the manager
# can only `.run()` once per instance, and we share the FastAPI app
# instance across test modules for performance).
os.environ["MCP_HTTP_ENABLED"] = "0"

# Hermetic data dir · isolated per-session temp · cleaned in fin
_TMP = Path(tempfile.mkdtemp(prefix="taotao-tests-"))
os.environ["CHROMA_DIR"] = str(_TMP / "chroma")
os.environ["CHECKPOINT_DB"] = str(_TMP / "checkpoints.sqlite")
os.environ["TRACE_DIR"] = str(_TMP / "traces")
(_TMP / "chroma").mkdir(parents=True, exist_ok=True)
(_TMP / "traces").mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_dir():
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def clean_memory():
    """Clear long-term memory between tests that exercise it.

    We need this because chroma collections persist across tests in the
    same session (per-tenant, but still).  Yields the LongTermMemory
    instance for the current tenant.
    """
    from agent.memory import get_memory
    mem = get_memory()
    try:
        mem.clear()
    except Exception:
        pass
    yield mem
    try:
        mem.clear()
    except Exception:
        pass
