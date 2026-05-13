"""Identity resolver · returns an Identity for every authenticated request.

Production deploy:
  1. Issue JWTs from your IdP / OAuth provider (Authentik / Auth0 / Cognito).
  2. Set `JWT_PUBLIC_KEY` (PEM) and `JWT_AUDIENCE` env vars.
  3. Optional: `JWT_ALGORITHMS` (default "RS256") and `JWT_TENANT_CLAIM`
     (default "tenant_id" · falls back to "tid", "org", "sub").

Dev deploy:
  - leave JWT_PUBLIC_KEY unset · falls back to legacy X-API-Key behaviour
    (single shared tenant) so existing localhost workflows keep working.

Why this matters · without per-tenant identity, ALL users share the same
chroma collection / sqlite checkpoint / profile · which is the #1 SaaS
data-leak risk.  See Book 24 · Phase 2 for the full discussion.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

log = logging.getLogger("agent.auth")

# Lazy import so installs without PyJWT keep working in dev mode.
try:
    import jwt  # PyJWT
    _HAS_JWT = True
except ImportError:  # pragma: no cover
    jwt = None  # type: ignore
    _HAS_JWT = False


@dataclass(frozen=True)
class Identity:
    """Authenticated caller · used everywhere downstream that needs to know
    *who* is making the request.

    `tenant_id` is the namespacing key for memory / chroma / traces.
    For legacy single-tenant deploys it's just "default".
    """
    user_id: str
    tenant_id: str
    email: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    raw: dict | None = None  # full JWT claims · only populated when JWT mode

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


# --------------------------------------------------------------------- env

def _jwt_public_key() -> str | None:
    """PEM-encoded public key used to verify RS256/ES256/EdDSA JWTs."""
    v = (os.environ.get("JWT_PUBLIC_KEY") or "").strip()
    return v or None


def _jwt_algorithms() -> list[str]:
    raw = (os.environ.get("JWT_ALGORITHMS") or "RS256").strip()
    return [a.strip() for a in raw.split(",") if a.strip()]


def _jwt_audience() -> str | None:
    v = (os.environ.get("JWT_AUDIENCE") or "").strip()
    return v or None


def _tenant_claim_names() -> list[str]:
    """Order matters · first hit wins."""
    primary = (os.environ.get("JWT_TENANT_CLAIM") or "tenant_id").strip()
    # always probe these too as graceful fallbacks
    return [primary, "tid", "org", "org_id", "workspace_id", "sub"]


def _api_key_expected() -> str | None:
    v = (os.environ.get("API_KEY") or "").strip()
    return v or None


# --------------------------------------------------------------------- core

def _identity_from_jwt(token: str) -> Identity:
    """Verify token and project to Identity.  Raises 401 on any failure."""
    if not _HAS_JWT:
        # Misconfigured prod · we promised JWT but PyJWT isn't installed.
        log.error("JWT_PUBLIC_KEY set but PyJWT not installed · pip install pyjwt[crypto]")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "JWT verification unavailable on server (pyjwt missing)",
        )
    pub = _jwt_public_key()
    aud = _jwt_audience()
    try:
        opts = {"verify_aud": bool(aud)}
        claims = jwt.decode(
            token, pub, algorithms=_jwt_algorithms(),
            audience=aud, options=opts,
        )
    except jwt.PyJWTError as e:
        log.info("JWT rejected · %s", e)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Invalid token: {e.__class__.__name__}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # tenant resolution · first claim that exists wins
    tenant: str | None = None
    for name in _tenant_claim_names():
        v = claims.get(name)
        if v:
            tenant = str(v)
            break
    if not tenant:
        # Hard fail · multi-tenant safety > convenience.  Customer needs
        # to configure their IdP to emit a tenant claim.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Token has no tenant claim · cannot resolve workspace",
        )

    user_id = str(claims.get("sub") or claims.get("user_id") or tenant)
    email = claims.get("email")
    roles_claim = claims.get("roles") or claims.get("scope") or ""
    if isinstance(roles_claim, str):
        roles = tuple(r.strip() for r in roles_claim.split() if r.strip())
    else:
        roles = tuple(str(r) for r in (roles_claim or ()))

    return Identity(
        user_id=user_id, tenant_id=tenant,
        email=email, roles=roles, raw=claims,
    )


def _extract_bearer(request: Request) -> str | None:
    """Read `Authorization: Bearer <token>` · case-insensitive."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    parts = h.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


# --------------------------------------------------------------------- dep

async def current_identity(request: Request) -> Identity:
    """FastAPI dependency · returns the caller's Identity.

    Resolution order:
      1. JWT_PUBLIC_KEY set + Authorization: Bearer <token> → validate token
      2. API_KEY set + X-API-Key header matches → synthetic shared identity
      3. Both unset → DEV MODE · synthetic anonymous identity
      4. None of the above → 401

    The `tenant_id` you get back is what every downstream component
    (memory, chroma, traces) MUST namespace by.
    """
    # JWT mode wins when configured · forces real identity in prod
    if _jwt_public_key():
        token = _extract_bearer(request)
        if not token:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Missing Authorization: Bearer <token>",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return _identity_from_jwt(token)

    # Shared key mode · single tenant
    expected = _api_key_expected()
    if expected:
        provided = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        if provided != expected:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Missing or invalid X-API-Key header",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return Identity(
            user_id="shared", tenant_id="default",
            email=None, roles=("user",),
        )

    # Dev mode · NO auth at all (fail loudly in startup logs · see app.py)
    return Identity(
        user_id="anonymous", tenant_id="dev",
        email=None, roles=("user",),
    )


def require_admin(ident: Identity) -> Identity:
    """Helper · raise 403 if not admin."""
    if not ident.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Admin role required",
        )
    return ident
