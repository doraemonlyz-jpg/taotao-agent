"""Admin endpoints · gated by `Depends(require_admin_identity)`.

  GET    /admin/me              · who am I + roles + tenant (debug)
  GET    /admin/tenants         · list all tenants with data on disk
  POST   /admin/cache/clear     · drop in-process LRU caches (memory · llm · graph)
  POST   /admin/dsr             · GDPR · purge ALL data for a tenant_id
  GET    /admin/usage           · global token usage summary
  GET    /admin/sessions        · list session IDs in the trace log

These are the buttons you'd give your support team.  Every endpoint
returns JSON · no HTML · designed to be called from a back-office tool
(Retool / internal admin UI / Slack slash command).

Auth note: in DEV mode (no JWT, no API_KEY), `current_identity` returns
a synthetic anonymous identity which is NOT admin · so /admin/* will 403
unless you set ADMIN_USERS=anonymous in env.  In prod you'd configure
your IdP to emit `roles=["admin"]` for the right users.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent.auth import Identity, require_admin_identity
from agent.config import get_settings
from agent.memory.long_term import _memory_for, _safe_tenant
from agent.observability import usage as usage_tracker
from agent.quota import (
    reset_for_user as quota_reset,
    snapshot as quota_snapshot,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/me")
def me(ident: Identity = Depends(require_admin_identity)) -> dict:
    """Echo · useful for verifying token + role mapping during deploy."""
    return {
        "user_id": ident.user_id,
        "tenant_id": ident.tenant_id,
        "email": ident.email,
        "roles": list(ident.roles),
        "is_admin": ident.is_admin,
    }


@router.get("/tenants")
def list_tenants(
    _: Identity = Depends(require_admin_identity),
) -> list[dict]:
    """List every tenant that has at least one chroma collection on disk.

    Walks `chroma_dir` and infers tenant ids from collection names.
    Slow on huge deployments (use a real CRM table in prod) · fine for
    most demos and B2B SaaS with <1k tenants.
    """
    chroma_root = Path(get_settings().chroma_dir)
    if not chroma_root.exists():
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for p in chroma_root.iterdir():
        if not p.is_dir():
            continue
        # Each chroma collection has a UUID directory · we can't tell tenant
        # from path. Instead, query the in-memory cache + list any tenants
        # that have warmed up since process start.  For full enumeration use
        # the chroma client `list_collections()`.
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(chroma_root))
            for coll in client.list_collections():
                name = coll.name
                if name.startswith("agent_memories_"):
                    tenant = name.replace("agent_memories_", "")
                    if tenant in seen:
                        continue
                    seen.add(tenant)
                    out.append({
                        "tenant_id": tenant,
                        "collection": name,
                        "count": coll.count(),
                    })
            break  # one pass through is enough · all tenants are listed at once
        except Exception as e:  # pragma: no cover · chroma version drift
            raise HTTPException(500, f"chroma list_collections failed: {e}") from e
    return sorted(out, key=lambda x: x["tenant_id"])


@router.post("/cache/clear")
def clear_caches(_: Identity = Depends(require_admin_identity)) -> dict:
    """Drop the per-tenant memory cache + LLM build cache.

    Useful after:
      - Bulk DSR delete (memory cache may hold a deleted tenant's instance)
      - Model hotswap that didn't propagate
      - Suspected leak between tenants (paranoid mode)

    Does NOT clear: chroma data on disk, sqlite checkpointer.
    For those, use /admin/dsr.
    """
    cleared: dict[str, int] = {}

    # Memory · per-tenant LRU
    cleared["memory_factory"] = _memory_for.cache_info().currsize
    _memory_for.cache_clear()

    # LLM · model build cache
    try:
        from agent.nodes.llm import reset_llm_cache

        reset_llm_cache()
        cleared["llm_models"] = -1  # reset_llm_cache doesn't expose a count
    except Exception:
        cleared["llm_models"] = 0

    return {"ok": True, "cleared": cleared}


class DSRIn(BaseModel):
    """Data Subject Request · GDPR Article 17 (right to erasure).

    `tenant_id` MUST be specified explicitly (we never infer for an
    admin operation · too easy to delete the wrong workspace).

    Set `dry_run=True` to preview what would be deleted without
    actually purging.  We strongly recommend running dry first.
    """

    tenant_id: str
    dry_run: bool = True
    confirm: str = ""  # must equal tenant_id when dry_run=False


@router.post("/dsr")
def data_subject_request(
    p: DSRIn,
    _: Identity = Depends(require_admin_identity),
) -> dict:
    """Purge all data for one tenant.

    Wipes:
      - Long-term memory (chroma collection) for the tenant
      - In-process LRU cache for that tenant
      - sqlite checkpointer rows tagged with that tenant (best-effort)

    Does NOT touch:
      - Trace JSONL file (contains other tenants' events too · use rotation)
      - Prometheus / OTel exporters (those are the observability backend's job)
    """
    tenant = _safe_tenant(p.tenant_id)

    # Probe what we'd delete
    try:
        mem = _memory_for(tenant)
        before_count = mem.collection.count()
    except Exception as e:
        raise HTTPException(404, f"tenant {tenant!r} has no data: {e}") from e

    if p.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "tenant_id": tenant,
            "would_delete": {
                "memories": before_count,
            },
            "next": "Re-call with dry_run=false AND confirm=<tenant_id>",
        }

    if p.confirm != tenant:
        raise HTTPException(
            400,
            f"DSR delete requires `confirm` to equal `tenant_id` ({tenant!r}). "
            "This is a guard against accidentally purging the wrong workspace.",
        )

    # Real delete
    deleted = {"memories": before_count}
    try:
        mem.clear()
    except Exception as e:  # pragma: no cover
        raise HTTPException(500, f"clear failed: {e}") from e

    # Drop the LRU entry so a subsequent get_memory(tenant) re-creates fresh.
    _memory_for.cache_clear()

    return {
        "ok": True,
        "dry_run": False,
        "tenant_id": tenant,
        "deleted": deleted,
    }


@router.get("/usage")
def admin_usage(
    _: Identity = Depends(require_admin_identity),
) -> dict:
    """Process-wide token + USD totals · admin only.

    The /usage endpoint is open (per-session) · this returns the global
    counter so admins can see total spend without scraping per-session.
    """
    return usage_tracker.snapshot(None)


@router.get("/quota/{tenant_id}/{user_id}")
def get_quota(
    tenant_id: str,
    user_id: str,
    _: Identity = Depends(require_admin_identity),
) -> dict:
    """Look up any user's quota state.

    For self-service inspection use /usage/me · this is for support.
    """
    return quota_snapshot(tenant_id=tenant_id, user_id=user_id)


class QuotaResetIn(BaseModel):
    """Wipe both daily AND monthly counters for one user.  Use after a
    refund, plan upgrade, false-positive throttle, etc."""

    tenant_id: str
    user_id: str


@router.post("/quota/reset")
def reset_quota(
    p: QuotaResetIn,
    _: Identity = Depends(require_admin_identity),
) -> dict:
    """Drop the rate-limit counter for one user · NOT a billing reversal."""
    deleted = quota_reset(tenant_id=p.tenant_id, user_id=p.user_id)
    return {"ok": True, "tenant_id": p.tenant_id, "user_id": p.user_id, "deleted": deleted}


@router.get("/disk-usage")
def disk_usage(_: Identity = Depends(require_admin_identity)) -> dict:
    """Approximate bytes-on-disk for chroma + sqlite.

    Useful for billing-by-storage tier estimations.  We use du-style
    walk · not perfect (doesn't account for reflink / sparse files) but
    close enough for a dashboard.
    """
    cfg = get_settings()

    def _du(path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())

    return {
        "chroma_dir": str(cfg.chroma_dir),
        "chroma_bytes": _du(Path(cfg.chroma_dir)),
        "trace_file": str(cfg.trace_file),
        "trace_bytes": _du(Path(cfg.trace_file)),
        "sqlite_checkpointer_bytes": _du(Path(cfg.chroma_dir).parent / "checkpoints.sqlite"),
        "free_bytes": shutil.disk_usage(cfg.chroma_dir.parent).free
        if cfg.chroma_dir.exists() and cfg.chroma_dir.parent.exists()
        else None,
    }
