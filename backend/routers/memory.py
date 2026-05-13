"""Memory endpoints · long-term facts / reflections / profile / skills.

Every read goes through `get_memory()` which is tenant-aware via the
identity middleware's ContextVar.  Writes are gated by API_KEY when set.

  GET    /memory                  · list (current tenant only)
  POST   /memory                  · add (current tenant)
  DELETE /memory                  · clear (current tenant)
  POST   /memory/prune            · drop low-value memories by composite score
  GET    /reflections             · list reflections
  DELETE /reflections             · clear reflections
  GET    /profile                 · all profile keys
  PUT    /profile                 · upsert one key
  DELETE /profile/{key}           · drop one key
  DELETE /profile                 · wipe profile
  GET    /skills                  · list discovered skills
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.memory import get_memory, get_profile, get_reflections, list_skills
from agent.security import require_api_key

router = APIRouter()


class MemoryIn(BaseModel):
    text: str
    kind: str = "fact"


@router.get("/memory", tags=["memory"])
def list_memory() -> list[dict]:
    return get_memory().list_all(limit=100)


@router.post("/memory", tags=["memory"], dependencies=[Depends(require_api_key)])
def add_memory(m: MemoryIn) -> dict:
    mid = get_memory().remember(m.text, kind=m.kind)
    return {"id": mid}


@router.delete("/memory", tags=["memory"], dependencies=[Depends(require_api_key)])
def clear_memory() -> dict:
    get_memory().clear()
    return {"ok": True}


class PruneIn(BaseModel):
    max_keep: int | None = None
    drop_fraction: float | None = None
    half_life_days: float | None = None
    dry_run: bool = False


@router.post("/memory/prune", tags=["memory"], dependencies=[Depends(require_api_key)])
def prune_memory(p: PruneIn) -> dict:
    """Drop low-value memories by composite recency+usage score.

    All knobs default to env-derived settings (AGENT_MEM_*).  Pass
    `dry_run: true` to preview what would go without deleting.
    """
    return get_memory().prune(
        max_keep=p.max_keep,
        drop_fraction=p.drop_fraction,
        half_life_days=p.half_life_days,
        dry_run=p.dry_run,
    )


@router.get("/reflections", tags=["memory"])
def list_reflections() -> list[dict]:
    return get_reflections().list_all(limit=100)


@router.delete(
    "/reflections", tags=["memory"], dependencies=[Depends(require_api_key)]
)
def clear_reflections() -> dict:
    get_reflections().clear()
    return {"ok": True}


class ProfileIn(BaseModel):
    key: str
    value: Any


@router.get("/profile", tags=["memory"])
def read_profile() -> dict:
    return get_profile().all()


@router.put("/profile", tags=["memory"], dependencies=[Depends(require_api_key)])
def update_profile(p: ProfileIn) -> dict:
    return get_profile().update(p.key, p.value)


@router.delete(
    "/profile/{key}", tags=["memory"], dependencies=[Depends(require_api_key)]
)
def delete_profile_key(key: str) -> dict:
    return get_profile().delete(key)


@router.delete(
    "/profile", tags=["memory"], dependencies=[Depends(require_api_key)]
)
def clear_profile() -> dict:
    get_profile().clear()
    return {"ok": True}


@router.get("/skills", tags=["memory"])
def list_skills_endpoint() -> list[dict]:
    return [
        {
            "name": s.name,
            "description": s.description,
            "when_to_use": s.when_to_use,
            "body": s.body,
            "path": str(s.path),
        }
        for s in list_skills()
    ]
