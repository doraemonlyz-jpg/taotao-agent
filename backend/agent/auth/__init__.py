"""Identity & multi-tenant auth · drop-in production replacement for
`require_api_key`.

Modes (env-gated · dev keeps working with zero config):

  - Dev (no JWT_PUBLIC_KEY, no API_KEY)  → synthetic identity tenant=dev
  - Shared key (API_KEY set)             → synthetic identity tenant=default
  - JWT (JWT_PUBLIC_KEY set)             → real per-user identity from token

Use:

    from agent.auth import current_identity, Identity

    @app.post("/chat", dependencies=[Depends(require_api_key)])  # legacy
    async def chat(ident: Identity = Depends(current_identity), ...): ...

The `tenant_id` is the namespacing key for memory / chroma / traces.
"""
from __future__ import annotations

from .identity import (
    ANONYMOUS,
    Identity,
    current_identity,
    get_current_identity,
    get_current_tenant_id,
    require_admin,
    require_admin_identity,
    use_identity,
)

__all__ = [
    "ANONYMOUS",
    "Identity",
    "current_identity",
    "get_current_identity",
    "get_current_tenant_id",
    "require_admin",
    "require_admin_identity",
    "use_identity",
]
