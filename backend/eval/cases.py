"""
Schemas + loader for golden test cases.

Each line of a golden/*.jsonl file is one `Case`:

    {
      "id": "math.001",
      "category": "math",
      "user": "What is sqrt(2^32 - 1)?",
      "expected_substrings": ["65535"],
      "expected_tools": ["calculator"],
      "must_not_contain": [],
      "rubric": "Should compute sqrt(2^32 - 1) ~ 65535.99...",
      "weight": 1.0,
      "tags": ["arithmetic", "single_tool"]
    }

Fields are intentionally minimal · the LLM judge handles fuzzy correctness;
the exact-substring + must-not-contain are programmatic guard-rails.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"


@dataclass
class Case:
    id: str
    category: str
    user: str
    expected_substrings: list[str] = field(default_factory=list)
    expected_tools: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    rubric: str = ""
    weight: float = 1.0
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Case":
        # Be lenient: strip unknown fields so authors can leave notes.
        keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in keys})


def load_all(category: str | None = None) -> list[Case]:
    """Load every case from `golden/*.jsonl` (or one file by category)."""
    files = (
        [GOLDEN_DIR / f"{category}.jsonl"]
        if category
        else sorted(GOLDEN_DIR.glob("*.jsonl"))
    )
    out: list[Case] = []
    for p in files:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(Case.from_dict(json.loads(line)))
            except Exception as e:  # pragma: no cover
                print(f"[warn] skipping malformed case in {p.name}: {e}")
    return out
