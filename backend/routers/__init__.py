"""FastAPI routers · split from app.py for navigability.

App.py is the assembly point · it builds the FastAPI instance, installs
middleware (CORS · identity · security · telemetry), then includes each
router below.  Routers don't know about each other · they share state
through `routers.shared` (rate-limit helper) and the global Identity
context-var.

Why split:
  - app.py was 773 lines / 29 endpoints · single file got slow to navigate.
  - Each router groups one concern · `chat` is the hottest, `meta` is read-only,
    `memory` is multi-tenant, `observability` is read-only and cheap.
  - Pattern matches FastAPI's official guidance · APIRouter + include_router.
"""
from . import admin, billing, chat, memory, meta, observability

__all__ = ["admin", "billing", "chat", "memory", "meta", "observability"]
