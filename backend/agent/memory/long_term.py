"""Long-term memory backed by Chroma. Stores facts/preferences/episodes
the user (or the agent itself) chooses to persist across sessions.

We use Chroma's default ONNX embedding model so the demo runs without an
OpenAI/HF embeddings key.

Decay / forget policy
---------------------
Memory only grows is a footgun · stale entries pile up, recall goes noisy.
Every `recall()` bumps the matched entries' `use_count` and refreshes
`last_used_at`, giving us LRU + frequency signals. `prune()` drops the
bottom-K entries by composite score:

    score = 0.7 * recency + 0.3 * usage
        recency = exp(-age_days / half_life_days)
        usage   = log1p(use_count) / log1p(max_use_count)

Defaults (`AGENT_MEM_HALF_LIFE_DAYS=14`, prune at >500 items, drop bottom 10%)
err on the side of keeping data · tune via env or call `prune(max_keep=...)`
explicitly.  Scheduled pruning is left to the operator (cron / endpoint).
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import get_settings


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _safe_tenant(tenant_id: str | None) -> str:
    """Coerce a tenant id into a chroma-collection-safe slug.

    Chroma collection names must match `^[a-zA-Z0-9._-]{3,63}$`.  We also
    enforce no leading/trailing dot.  Anything weird falls back to "default".
    """
    import re
    raw = (tenant_id or "default").strip()
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip(".-_")
    if not (3 <= len(slug) <= 50):
        return "default"
    return slug or "default"


def _collection_name(tenant: str) -> str:
    return f"agent_memories_{_safe_tenant(tenant)}"


class LongTermMemory:
    """Per-tenant chroma-backed long-term memory.

    Each tenant gets its own chroma collection · this is the
    *namespacing* primitive that keeps user A's memories from leaking
    into user B's `recall()` results.

    Backward-compat: `LongTermMemory()` with no args = the legacy
    "default" tenant collection · so existing single-tenant callers keep
    working.  Multi-tenant callers should use `LongTermMemory.for_tenant(tid)`
    or pass `tenant_id="..."` directly.
    """

    def __init__(self, tenant_id: str | None = None) -> None:
        cfg = get_settings()
        self.tenant_id = _safe_tenant(tenant_id)
        self.client = chromadb.PersistentClient(
            path=str(cfg.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # Per-tenant collection · `agent_memories_<tenant>` ·
        # strong isolation, no risk of forgetting a where-clause filter.
        self.collection = self.client.get_or_create_collection(
            name=_collection_name(self.tenant_id),
            metadata={"hnsw:space": "cosine", "tenant": self.tenant_id},
        )

    @classmethod
    def for_tenant(cls, tenant_id: str | None) -> "LongTermMemory":
        """Convenience constructor · `mem = LongTermMemory.for_tenant(ident.tenant_id)`"""
        return cls(tenant_id=tenant_id)

    def remember(self, text: str, kind: str = "fact", session_id: str | None = None) -> str:
        mem_id = str(uuid.uuid4())
        now = _utc_now_iso()
        self.collection.add(
            ids=[mem_id],
            documents=[text],
            metadatas=[{
                "kind": kind,
                "session_id": session_id or "",
                "ts": now,
                "last_used_at": now,
                "use_count": 0,
            }],
        )
        return mem_id

    def remember_if_new(
        self,
        text: str,
        kind: str = "fact",
        session_id: str | None = None,
        threshold: float | None = None,
    ) -> str | None:
        """Write only if no near-duplicate (cosine similarity > threshold) exists.
        Returns the new id, or None if skipped as a duplicate."""
        from ..config import get_settings
        thr = threshold if threshold is not None else get_settings().dedup_threshold
        if self.collection.count() > 0:
            try:
                hit = self.collection.query(
                    query_texts=[text],
                    n_results=1,
                    include=["distances"],
                )
                dists = (hit.get("distances") or [[]])[0]
                if dists:
                    sim = 1.0 - float(dists[0])
                    if sim >= thr:
                        return None
            except Exception:
                pass
        return self.remember(text, kind=kind, session_id=session_id)

    # ------------------------------------------------------------------ recall
    def _bump_usage(self, ids: list[str]) -> None:
        """Refresh last_used_at and increment use_count for ids that were
        just returned to the agent. Tolerant of races / missing rows."""
        if not ids:
            return
        try:
            current = self.collection.get(ids=ids, include=["metadatas"])
        except Exception:
            return
        cur_ids = current.get("ids") or []
        cur_metas = current.get("metadatas") or []
        if not cur_ids:
            return
        now = _utc_now_iso()
        new_metas = []
        for meta in cur_metas:
            m = dict(meta or {})
            m["use_count"] = int(m.get("use_count", 0) or 0) + 1
            m["last_used_at"] = now
            new_metas.append(m)
        try:
            self.collection.update(ids=cur_ids, metadatas=new_metas)
        except Exception:
            pass

    def recall(self, query: str, k: int = 4) -> list[str]:
        if self.collection.count() == 0:
            return []
        result = self.collection.query(
            query_texts=[query],
            n_results=min(k, self.collection.count()),
            include=["documents", "metadatas"],
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        self._bump_usage(ids)
        return docs

    def recall_hyde(self, query: str, k: int = 4) -> list[str]:
        """Hypothetical-Document Embedding retrieval. Lets a small LLM
        fabricate the *kind* of memory we'd expect to see if it existed,
        then embeds THAT instead of the raw query. Boosts recall on
        keyword-poor questions ("我以前提过什么偏好？")."""
        if self.collection.count() == 0:
            return []
        try:
            from ..nodes.llm import get_fast_llm
            from langchain_core.messages import HumanMessage
            prompt = (
                "Write 1-2 short sentences that would PLAUSIBLY appear in a "
                "personal-memory store and would answer the following user "
                "question. Do NOT explain — just write the sentences.\n\n"
                f"Question: {query}"
            )
            resp = get_fast_llm(temperature=0.3).invoke([HumanMessage(content=prompt)])
            hypo = resp.content if isinstance(resp.content, str) else str(resp.content)
            blended = (hypo + "\n" + query).strip()
        except Exception:
            blended = query
        result = self.collection.query(
            query_texts=[blended],
            n_results=min(k, self.collection.count()),
            include=["documents", "metadatas"],
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        self._bump_usage(ids)
        return docs

    # ------------------------------------------------------------------ list/clear
    def list_all(self, limit: int = 50) -> list[dict]:
        if self.collection.count() == 0:
            return []
        out = self.collection.get(limit=limit)
        items = []
        for i, doc in enumerate(out.get("documents") or []):
            items.append({
                "id": (out.get("ids") or [None])[i],
                "text": doc,
                "metadata": (out.get("metadatas") or [{}])[i],
            })
        return items

    def clear(self) -> None:
        ids = self.collection.get().get("ids") or []
        if ids:
            self.collection.delete(ids=ids)

    # ------------------------------------------------------------------ decay/prune
    def _score(self, meta: dict, max_use: int, half_life_days: float) -> float:
        """Composite ranking — lower = more droppable."""
        last = _parse_iso(meta.get("last_used_at")) or _parse_iso(meta.get("ts"))
        if last is None:
            recency = 0.0
        else:
            # Pre-decay-feature memories were stored with naive UTC strings.
            # Coerce to aware so the subtraction below doesn't TypeError.
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - last).total_seconds() / 86400)
            recency = math.exp(-age_days / max(half_life_days, 1.0))
        use_count = int(meta.get("use_count", 0) or 0)
        usage = math.log1p(use_count) / math.log1p(max(max_use, 1))
        return 0.7 * recency + 0.3 * usage

    def prune(
        self,
        *,
        max_keep: int | None = None,
        drop_fraction: float | None = None,
        half_life_days: float | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Drop low-value memories until the collection fits `max_keep`.

        Strategy:
          - If `max_keep` is set and current size <= max_keep, no-op.
          - Otherwise rank everything by composite score and drop either the
            overflow (when `max_keep` set) or the bottom `drop_fraction`
            (default 10%).

        Returns a summary dict.  `dry_run=True` skips the actual delete and
        just reports who *would* go.
        """
        cfg = get_settings()
        if max_keep is None:
            max_keep = cfg.mem_prune_max_keep
        if drop_fraction is None:
            drop_fraction = cfg.mem_prune_drop_fraction
        if half_life_days is None:
            half_life_days = cfg.mem_half_life_days

        total = self.collection.count()
        if total == 0:
            return {"total": 0, "kept": 0, "dropped": 0, "dry_run": dry_run}

        # Pull everything · this is bounded by max_keep ceiling so cheap.
        out = self.collection.get(include=["metadatas"])
        ids = out.get("ids") or []
        metas = out.get("metadatas") or []
        if not ids:
            return {"total": 0, "kept": 0, "dropped": 0, "dry_run": dry_run}

        max_use = max((int(m.get("use_count", 0) or 0) for m in metas), default=1)

        scored = sorted(
            zip(ids, metas, strict=False),
            key=lambda p: self._score(dict(p[1] or {}), max_use, half_life_days),
        )

        if max_keep > 0 and total > max_keep:
            n_drop = total - max_keep
        else:
            n_drop = max(0, int(total * drop_fraction))

        # Floor for safety: never delete more than 50% in one call · prevents
        # accidental "prune() called with bad args wipes the store".
        n_drop = min(n_drop, total // 2)

        victims = [pid for pid, _ in scored[:n_drop]]
        if not dry_run and victims:
            try:
                self.collection.delete(ids=victims)
            except Exception:
                pass

        return {
            "total": total,
            "kept": total - len(victims),
            "dropped": len(victims),
            "dry_run": dry_run,
            "policy": {
                "max_keep": max_keep,
                "drop_fraction": drop_fraction,
                "half_life_days": half_life_days,
            },
            "victim_preview": victims[:5],
        }


@lru_cache(maxsize=64)
def _memory_for(tenant: str) -> LongTermMemory:
    """Memoised per-tenant factory · 64 most-recently-used tenants stay hot.
    Eviction is fine · chroma reopens cheaply.  64 covers most B2B SaaS;
    bump if you grow."""
    return LongTermMemory(tenant_id=tenant)


def get_memory(tenant_id: str | None = None) -> LongTermMemory:
    """Return the LongTermMemory for `tenant_id` · or the current
    request's tenant from the auth ContextVar · or "default".

    Single canonical entry-point used everywhere (graph nodes, harness
    tools, /memory endpoints).  Multi-tenancy is ENFORCED HERE · pass a
    tenant_id to bypass the ContextVar (admin operations, migrations).
    """
    if tenant_id is None:
        # Lazy import · avoid circular (auth → security → app → memory)
        from ..auth import get_current_tenant_id
        tenant_id = get_current_tenant_id()
    return _memory_for(_safe_tenant(tenant_id))
