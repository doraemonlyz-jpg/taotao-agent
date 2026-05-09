"""Runtime configuration. Reads .env once and exposes typed settings."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings(BaseModel):
    # Tiered models — `model` is the powerful one used by the executor & sub-agents,
    # `fast_model` is a cheap/quick one used by router-style nodes
    # (planner, summarizer, critic, extractor). Falls back to `model` if not set.
    model: str = os.getenv("AGENT_MODEL", "anthropic:claude-sonnet-4-5-20250929")
    fast_model: str = os.getenv("AGENT_FAST_MODEL", "anthropic:claude-haiku-4-5")

    workdir: Path = Path(os.getenv("AGENT_WORKDIR", str(ROOT / "data" / "workspace")))
    chroma_dir: Path = Path(os.getenv("CHROMA_DIR", str(ROOT / "data" / "chroma")))
    trace_file: Path = Path(os.getenv("TRACE_FILE", str(ROOT / "data" / "traces.jsonl")))
    checkpoint_db: Path = Path(os.getenv("CHECKPOINT_DB", str(ROOT / "data" / "checkpoints.sqlite")))

    max_loop_iters: int = 8
    critic_enabled: bool = True
    guardrails_enabled: bool = True

    # Per-session USD budget. 0 = unlimited.
    session_budget_usd: float = float(os.getenv("AGENT_SESSION_BUDGET_USD", "0.50"))

    # Tool-execution knobs
    tool_timeout_s: float = float(os.getenv("AGENT_TOOL_TIMEOUT_S", "30"))
    tool_result_max_chars: int = int(os.getenv("AGENT_TOOL_RESULT_MAX_CHARS", "4000"))

    # Memory hygiene
    dedup_threshold: float = float(os.getenv("AGENT_DEDUP_THRESHOLD", "0.92"))

    # Tool routing — top-K relevant tools to bind per turn (0 = bind all)
    tool_route_topk: int = int(os.getenv("AGENT_TOOL_ROUTE_TOPK", "0"))

    def ensure_dirs(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


def update_runtime_model(model: str | None = None, fast_model: str | None = None) -> Settings:
    """Mutate the cached Settings in place — used by `POST /model` so the
    frontend can swap models without restarting the backend.

    Returns the live Settings object after mutation.
    """
    s = get_settings()
    if model:
        s.model = model
    if fast_model:
        s.fast_model = fast_model
    return s
