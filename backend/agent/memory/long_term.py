"""Long-term memory backed by Chroma. Stores facts/preferences/episodes
the user (or the agent itself) chooses to persist across sessions.

We use Chroma's default ONNX embedding model so the demo runs without an
OpenAI/HF embeddings key."""
from __future__ import annotations

import uuid
from datetime import datetime
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import get_settings


class LongTermMemory:
    def __init__(self) -> None:
        cfg = get_settings()
        self.client = chromadb.PersistentClient(
            path=str(cfg.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="agent_memories",
            metadata={"hnsw:space": "cosine"},
        )

    def remember(self, text: str, kind: str = "fact", session_id: str | None = None) -> str:
        mem_id = str(uuid.uuid4())
        self.collection.add(
            ids=[mem_id],
            documents=[text],
            metadatas=[{
                "kind": kind,
                "session_id": session_id or "",
                "ts": datetime.utcnow().isoformat(),
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
                    # cosine distance → similarity = 1 - distance
                    sim = 1.0 - float(dists[0])
                    if sim >= thr:
                        return None
            except Exception:
                pass
        return self.remember(text, kind=kind, session_id=session_id)

    def recall(self, query: str, k: int = 4) -> list[str]:
        if self.collection.count() == 0:
            return []
        result = self.collection.query(query_texts=[query], n_results=min(k, self.collection.count()))
        docs = result.get("documents") or [[]]
        return docs[0] if docs else []

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
        )
        docs = result.get("documents") or [[]]
        return docs[0] if docs else []

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


@lru_cache(maxsize=1)
def get_memory() -> LongTermMemory:
    return LongTermMemory()
