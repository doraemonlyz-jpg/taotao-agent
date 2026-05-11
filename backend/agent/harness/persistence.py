"""Per-session message-list persistence — the harness equivalent of the
graph's SQLite checkpointer.

Why JSON files instead of SQLite?

  - The graph version uses LangGraph's `AsyncSqliteSaver`, which serialises
    a complex `AgentState` TypedDict at every superstep.  Necessary because
    LangGraph manages state with reducers per-channel.
  - The harness has ONE channel: `messages`.  Saving = `json.dump(messages, f)`.
    Restoring = `json.load(f)`.  No schema, no reducers, no migrations.
  - For real production scale, swap the `JSONFileStore` impl for a
    `RedisStore` or `PostgresStore` — the interface is 4 methods.

Trade-offs you accept:
  - One file per session (fine until you have ≳100k sessions; then move).
  - No transactional updates (a write happens after every loop step;
    on crash you may lose ONE step at most).
  - Concurrent writes to the same session are last-writer-wins (we
    therefore process each session serially in the loop).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langchain_core.load import dumps as lc_dumps, loads as lc_loads
from langchain_core.messages import BaseMessage

from ..config import get_settings


class HarnessSessionStore:
    """File-backed message-list store · `data/harness/<session_id>.json`."""

    def __init__(self, root: Path | None = None) -> None:
        cfg = get_settings()
        # checkpoint_db lives at data/checkpoints.sqlite — put us next to it
        self.root = root or (cfg.checkpoint_db.parent / "harness")
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id: str) -> Path:
        # Filename safety — session_ids are UUIDs from the API but be defensive.
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self.root / f"{safe}.json"

    def load(self, session_id: str) -> list[BaseMessage]:
        """Returns the persisted message list — or an empty list if new.

        Uses LangChain's `loads` so we round-trip every message subtype
        (HumanMessage, AIMessage with tool_calls, ToolMessage, …) faithfully.
        """
        p = self.path(session_id)
        if not p.exists():
            return []
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            return [lc_loads(json.dumps(m)) for m in data]
        except (OSError, json.JSONDecodeError, ValueError):
            # Corrupt file = treat as new session.  Better than crashing.
            return []

    def save(self, session_id: str, messages: list[BaseMessage]) -> None:
        """Atomic write — temp file + rename — so a crash mid-write
        leaves the previous good copy intact."""
        p = self.path(session_id)
        tmp = p.with_suffix(".json.tmp")
        # lc_dumps returns a JSON string per message; we wrap in an array
        serialised = [json.loads(lc_dumps(m)) for m in messages]
        tmp.write_text(json.dumps(serialised, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def append(self, session_id: str, *new: BaseMessage) -> list[BaseMessage]:
        """Convenience: load → extend → save → return.  Used after each
        loop step so a crash never leaves the message list inconsistent."""
        msgs = self.load(session_id)
        msgs.extend(new)
        self.save(session_id, msgs)
        return msgs

    def clear(self, session_id: str) -> None:
        p = self.path(session_id)
        if p.exists():
            p.unlink()


# Module-level singleton — same pattern as memory/profile/skills.
_default: HarnessSessionStore | None = None


def get_store() -> HarnessSessionStore:
    global _default
    if _default is None:
        _default = HarnessSessionStore()
    return _default
