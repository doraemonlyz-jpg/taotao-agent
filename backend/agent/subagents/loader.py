"""Subagent-as-markdown · drop a `.md` in `data/subagents/` to register a
new role available via `dispatch_subagent(role="my-role", ...)`.

Frontmatter schema (YAML-flat key:value):

  ---
  name: legal-reviewer
  description: Reviews contracts for liability + termination risk
  tools: read_file, web_search, write_file
  max_steps: 6
  ---
  You are LEGAL-REVIEWER · ...

The body becomes the system prompt of the sub-agent.  `tools` is a
comma-separated list of tool names known to the harness registry; unknown
names are silently skipped (with a warning trace).

Built-in roles in `subagent.py` win on name collision (so users can't
accidentally break the supervisor agent by overriding "researcher").  The
loader is called lazily by `subagent.py::_ROLES`-resolution path, so a new
.md file is picked up on the next /chat call without restarting the server.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import get_settings
from ..memory.skills import _parse_frontmatter  # reuse the tiny parser


@dataclass(frozen=True)
class MdSubagent:
    name: str
    description: str
    system: str
    tool_names: tuple[str, ...]
    max_steps: int
    path: Path


def _subagents_dir() -> Path:
    cfg = get_settings()
    p = cfg.workdir.parent / "subagents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_md_subagents() -> list[MdSubagent]:
    out: list[MdSubagent] = []
    for f in sorted(_subagents_dir().glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        tools_raw = meta.get("tools", "")
        tool_names = tuple(t.strip() for t in tools_raw.split(",") if t.strip())
        try:
            max_steps = int(meta.get("max_steps", "6"))
        except ValueError:
            max_steps = 6
        out.append(MdSubagent(
            name=meta.get("name") or f.stem,
            description=meta.get("description", ""),
            system=body.strip(),
            tool_names=tool_names,
            max_steps=max_steps,
            path=f,
        ))
    return out


def get_md_subagent(name: str) -> MdSubagent | None:
    for s in list_md_subagents():
        if s.name == name:
            return s
    return None


def _all_tools_by_name() -> dict:
    """Collect harness tool registry · keyed by tool.name.  Lazy import to
    avoid circulars."""
    from ..harness.tools import HARNESS_TOOLS
    return {t.name: t for t in HARNESS_TOOLS}


def resolve_tools(tool_names: tuple[str, ...]) -> list:
    by_name = _all_tools_by_name()
    return [by_name[n] for n in tool_names if n in by_name]
