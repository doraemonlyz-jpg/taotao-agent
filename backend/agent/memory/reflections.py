"""Reflection memory — separate Chroma collection for 'lessons learned'.

Whereas `LongTermMemory` stores user-facing facts ("user is allergic
to peanuts"), reflections store agent-facing lessons:

  • critic feedback that triggered a revision
  • post-mortems on tool failures
  • workflow shortcuts the agent discovered

Recalled at perception time so the agent enters each turn with
relevant prior wisdom."""
from __future__ import annotations

import uuid
from datetime import datetime
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import get_settings


class ReflectionMemory:
    def __init__(self) -> None:
        cfg = get_settings()
        self.client = chromadb.PersistentClient(
            path=str(cfg.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="agent_reflections",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, text: str, *, source: str = "critic", session_id: str | None = None) -> str:
        rid = str(uuid.uuid4())
        self.collection.add(
            ids=[rid],
            documents=[text],
            metadatas=[{
                "source": source,
                "session_id": session_id or "",
                "ts": datetime.utcnow().isoformat(),
            }],
        )
        return rid

    def add_if_new(
        self,
        text: str,
        *,
        source: str = "critic",
        session_id: str | None = None,
        threshold: float | None = None,
    ) -> str | None:
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
        return self.add(text, source=source, session_id=session_id)

    def recall(self, query: str, k: int = 2) -> list[str]:
        if self.collection.count() == 0:
            return []
        result = self.collection.query(
            query_texts=[query],
            n_results=min(k, self.collection.count()),
        )
        docs = result.get("documents") or [[]]
        return docs[0] if docs else []

    def list_all(self, limit: int = 50) -> list[dict]:
        if self.collection.count() == 0:
            return []
        out = self.collection.get(limit=limit)
        items: list[dict] = []
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


@lru_cache(maxsize=1)
def get_reflections() -> ReflectionMemory:
    return ReflectionMemory()
