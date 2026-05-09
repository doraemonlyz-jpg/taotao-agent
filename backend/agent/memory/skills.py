"""Procedural memory — markdown skills loaded from `data/skills/*.md`.

Inspired by Anthropic Skills (Oct 2025): each skill is a markdown file with
YAML frontmatter (`name`, `description`, optional `when_to_use`).
The skill INDEX (name + description) is always injected into the prompt so
the LLM knows what's available; the BODY is loaded on-demand via the
`load_skill` tool. Keeps the prompt light while giving the agent a library
of recipes."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import get_settings


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    when_to_use: str
    body: str
    path: Path


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Tiny YAML-frontmatter parser. We only need flat key:value pairs."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    front_raw = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    meta: dict[str, str] = {}
    for ln in front_raw.splitlines():
        if ":" in ln:
            k, _, v = ln.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


@lru_cache(maxsize=1)
def _skills_dir() -> Path:
    cfg = get_settings()
    p = cfg.workdir.parent / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_skills() -> list[Skill]:
    out: list[Skill] = []
    for f in sorted(_skills_dir().glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        out.append(Skill(
            name=meta.get("name") or f.stem,
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use", ""),
            body=body.strip(),
            path=f,
        ))
    return out


def get_skill(name: str) -> Skill | None:
    for s in list_skills():
        if s.name == name:
            return s
    return None


def skills_index_block() -> str:
    """Compact list inserted into the system prompt so the LLM knows
    what skills exist. Keep each line short."""
    skills = list_skills()
    if not skills:
        return ""
    lines = []
    for s in skills:
        line = f"- {s.name}: {s.description}"
        if s.when_to_use:
            line += f" (when: {s.when_to_use})"
        lines.append(line)
    return (
        "[Available skills — call `load_skill(name)` to fetch the full recipe]\n"
        + "\n".join(lines)
    )
