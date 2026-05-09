"""Lightweight tool router.

When `Settings.tool_route_topk` > 0, instead of binding all 11 tools to
the executor LLM (~3-4k tokens of schema bloat per call), we score each
tool's relevance to the current user query via a one-shot embedding
similarity over the tool descriptions and bind only the top-K.

Re-uses Chroma's default ONNX embeddings (already loaded for memory) so
this adds almost no startup cost.

Some tools are *always* available regardless of routing — they're
small, cheap, and the agent occasionally needs them out of left field
(e.g. `remember`, `recall`, profile/skill ops)."""
from __future__ import annotations

from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import get_settings
from .registry import all_tools

# Tools that should always be present regardless of routing.
ALWAYS_ON = {
    "remember",
    "recall",
    "update_profile",
    "read_profile",
    "load_skill",
}


@lru_cache(maxsize=1)
def _index():
    """Build (and cache) an in-memory Chroma collection of tool descriptions."""
    cfg = get_settings()
    client = chromadb.PersistentClient(
        path=str(cfg.chroma_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    coll = client.get_or_create_collection(
        name="agent_tool_router",
        metadata={"hnsw:space": "cosine"},
    )
    # rebuild every process start to keep tool list fresh
    existing = coll.get().get("ids") or []
    if existing:
        coll.delete(ids=existing)
    coll.add(
        ids=[t.name for t in all_tools],
        documents=[(t.description or t.name).strip() for t in all_tools],
    )
    return coll


def select_tools(query: str):
    """Return the subset of tools to bind for this turn."""
    cfg = get_settings()
    k = cfg.tool_route_topk
    if k <= 0 or k >= len(all_tools):
        return list(all_tools)

    try:
        coll = _index()
        hits = coll.query(query_texts=[query], n_results=min(k, len(all_tools)))
        chosen = set((hits.get("ids") or [[]])[0])
    except Exception:
        return list(all_tools)

    chosen.update(ALWAYS_ON)
    return [t for t in all_tools if t.name in chosen]
