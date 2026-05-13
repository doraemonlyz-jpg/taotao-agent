"""FastAPI surface · assembly point only.

App.py is intentionally thin · all endpoint logic lives in `routers/`.
The order here is load-bearing:

  1. Build MCP server (must exist before app instance · its lifespan attaches)
  2. Construct FastAPI app + middleware (CORS · identity)
  3. install_security → app.state.limiter set
  4. install_telemetry → /metrics + auto-instrument
  5. set_limiter on routers.shared (so chat router decorators see it)
  6. import + include routers
  7. Mount MCP + tutorial static

Routers:
  - meta          · /health · /tools · /models · /model · /permissions* · /hooks · /config · /notify
  - chat          · /chat · /chat/v2 · /chat/replay · /chat/replay/sessions · /chat/v2/tools · /chat/v2/session/{id}
  - memory        · /memory* · /reflections* · /profile* · /skills
  - observability · /traces · /usage
"""
from __future__ import annotations

import contextlib
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent.auth import Identity, use_identity
from agent.auth.identity import _api_key_expected, _identity_from_jwt, _jwt_public_key
from agent.mcp.client import get_status as mcp_client_status  # noqa: F401  (used by /health via routers.meta)
from agent.mcp.server import build_mcp_server
from agent.observability import configure_logging, install_telemetry
from agent.security import install_security

# Configure structured logging FIRST · before anything else logs. Reads
# LOG_FORMAT (json|pretty) and LOG_LEVEL from env. JSON in prod (Docker),
# pretty in dev (TTY). All `agent.*` loggers automatically pick this up.
configure_logging()

# --------------------------------------------------------------------- #
# 1. MCP server build · before FastAPI() so its lifespan can attach.
# --------------------------------------------------------------------- #
MCP_STATUS: dict = {"http_enabled": False, "exposed_tools": []}
_mcp_instance = None
if os.environ.get("MCP_HTTP_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"):
    try:
        _mcp_instance = build_mcp_server()
        MCP_STATUS = {
            "http_enabled": True,
            "endpoint": "/mcp",
            "transport": "streamable_http",
            "exposed_tools": [t.name for t in _mcp_instance._tool_manager.list_tools()],
        }
    except Exception as e:  # pragma: no cover · degrade gracefully
        MCP_STATUS = {"http_enabled": False, "error": repr(e)}


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Start FastMCP's StreamableHTTP session manager.

    Mounted ASGI sub-apps don't inherit the parent's lifespan events, so
    FastMCP's task group never starts and every request 500s with
    'Task group is not initialized'. We bridge it here.
    """
    if _mcp_instance is not None:
        async with _mcp_instance.session_manager.run():
            yield
    else:
        yield


# --------------------------------------------------------------------- #
# 2. FastAPI app + CORS + identity middleware
# --------------------------------------------------------------------- #
app = FastAPI(
    title="桃桃 Agent · taotao-agent",
    version="0.2.0",
    description=(
        "Production-shape Agent demo. Two interchangeable backends:\n"
        "- `POST /chat`     · LangGraph 13-node graph\n"
        "- `POST /chat/v2`  · Harness while-loop (Claude-Code style)\n\n"
        "Both share SSE wire format, tool registry, memory, and observability.\n"
        "MCP-compatible · `/mcp` exposes whitelisted tools to Claude Desktop, Cursor, and other MCP clients."
    ),
    lifespan=_lifespan,
)

# CORS · default to "*" for dev demo. Tighten via ALLOWED_ORIGINS env when
# you put this behind a real domain (comma-separated list).
_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)


# Identity middleware · resolves the caller's Identity once per request
# and stashes it in a ContextVar so deep-stack code (memory tools, graph
# nodes, harness loop) can read `get_current_identity()` without having
# to plumb the request object through every call site.
#
# This is the bridge that makes `get_memory()` (no args) actually
# tenant-aware · without it, every request still leaks into the
# "default" tenant collection.
#
# Resolution mirrors `current_identity` dependency exactly · just runs
# earlier (middleware) so it's set before any tool runs.  Failures here
# do NOT 401 · we fall through to the dependency on the actual route
# (which DOES 401).  Middleware errors would 500 the whole app.
@app.middleware("http")
async def _identity_middleware(request: Request, call_next):
    ident = None
    try:
        if _jwt_public_key():
            auth_h = request.headers.get("authorization") or request.headers.get("Authorization")
            if auth_h:
                parts = auth_h.split(None, 1)
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    ident = _identity_from_jwt(parts[1].strip())
        elif _api_key_expected():
            provided = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
            if provided == _api_key_expected():
                ident = Identity(
                    user_id="shared", tenant_id="default",
                    email=None, roles=("user",),
                )
    except Exception:
        # Any auth error here is a bad header · downstream Depends() will
        # 401 properly.  We MUST NOT raise from middleware.
        ident = None

    with use_identity(ident):
        return await call_next(request)


# --------------------------------------------------------------------- #
# 3. install_security must run BEFORE we set_limiter on shared module
#    (chat router decorators read the limiter at import time).
# --------------------------------------------------------------------- #
SECURITY_STATUS = install_security(app)
LIMITER = getattr(app.state, "limiter", None)

# 4. OpenTelemetry + Prometheus · auto-instruments FastAPI + httpx.
#    /metrics endpoint is registered here. Tools / sub-agents / LLM calls
#    get hand-instrumented via tool_span / subagent_span / llm_span.
TELEMETRY_STATUS = install_telemetry(app)


# --------------------------------------------------------------------- #
# 5. Inject limiter into router.shared · MUST happen before importing routers.
# --------------------------------------------------------------------- #
from routers.shared import set_limiter  # noqa: E402  (intentional ordering)

set_limiter(LIMITER)


# --------------------------------------------------------------------- #
# 6. Import routers (now that the limiter is set) · include them on app.
# --------------------------------------------------------------------- #
from routers import admin, billing, chat, memory, meta, observability  # noqa: E402

meta.set_feature_status(
    security=SECURITY_STATUS,
    telemetry=TELEMETRY_STATUS,
    mcp_server=MCP_STATUS,
)

app.include_router(meta.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(observability.router)
app.include_router(admin.router)  # /admin/* · all gated by require_admin_identity
app.include_router(billing.router)  # /billing/* · Stripe checkout/portal/webhook


# --------------------------------------------------------------------- #
# 7. Mount MCP streamable-http app at /mcp · POST/GET http://host:8000/mcp/
# --------------------------------------------------------------------- #
if _mcp_instance is not None:
    app.mount("/mcp", _mcp_instance.streamable_http_app())

# Mount the book site (docs/) at /tutorial so the frontend can deep-link
# to any chapter from the topbar without a separate static server.  We
# avoid /docs because FastAPI already mounts Swagger UI there.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_DOCS_DIR = os.path.join(ROOT_DIR, "docs")
if os.path.isdir(_DOCS_DIR):
    app.mount("/tutorial", StaticFiles(directory=_DOCS_DIR, html=True), name="tutorial")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
