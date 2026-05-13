"""Project-level instructions · `AGENTS.md` / `TAOTAO.md` auto-injection.

Mirrors what Claude Code does with `CLAUDE.md` and what Cursor does with
`AGENTS.md` (Anthropic's open spec, https://agents.md): if a file with that
name exists in the working directory (or any parent up to /), its contents
are pasted verbatim into the agent's system prompt under a clear header.

Lookup order (first match wins):
  1. `<cwd>/AGENTS.md`
  2. `<cwd>/TAOTAO.md`
  3. walk up cwd's parents, repeat 1-2 in each
  4. `~/.taotao/AGENTS.md`        (global fallback)

Cap: 16 KiB to avoid bloating every system prompt; trim with a clear marker
if the file is larger.

The same loader is invoked by both the harness (in `prompt.render_system_prompt`)
and the graph (via `nodes/perception.py` if you wire it there).  It re-reads
on every turn — no cache — so editing AGENTS.md is reflected immediately
without restarting the backend.  Cost is one filesystem stat per turn.
"""
from __future__ import annotations

from pathlib import Path

MAX_BYTES = 16 * 1024
CANDIDATE_NAMES = ("AGENTS.md", "TAOTAO.md", "CLAUDE.md")


def _candidates() -> list[Path]:
    cwd = Path.cwd()
    out: list[Path] = []
    for d in [cwd, *cwd.parents]:
        for name in CANDIDATE_NAMES:
            out.append(d / name)
    out.append(Path.home() / ".taotao" / "AGENTS.md")
    return out


def load() -> tuple[str | None, Path | None]:
    """Return (text, source-path) or (None, None) if nothing found."""
    for p in _candidates():
        try:
            if not p.is_file():
                continue
            raw = p.read_bytes()
        except OSError:
            continue
        if len(raw) > MAX_BYTES:
            head = raw[: MAX_BYTES - 200].decode("utf-8", errors="ignore")
            return (
                head + f"\n\n…[AGENTS.md truncated · was {len(raw)} bytes · cap {MAX_BYTES}]…",
                p,
            )
        try:
            return raw.decode("utf-8"), p
        except UnicodeDecodeError:
            continue
    return None, None


def system_block() -> str:
    """Renderable block for splicing into a system prompt · empty if nothing."""
    text, src = load()
    if not text:
        return ""
    return (
        "\n\n────────────────────────────────────────\n"
        f"[Project instructions · loaded from {src}]\n"
        "These rules come from the user's repository · always-on context.\n"
        "Treat them as the user speaking · obey unless they conflict with\n"
        "core safety guardrails.\n"
        "────────────────────────────────────────\n"
        f"{text.strip()}\n"
    )
