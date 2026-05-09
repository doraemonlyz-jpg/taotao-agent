"""Read / write files inside a jailed workspace dir."""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from ..config import get_settings


def _resolve(relpath: str) -> Path:
    """Resolve `relpath` inside the jailed workdir; refuse traversal."""
    workdir = get_settings().workdir.resolve()
    target = (workdir / relpath).resolve()
    if workdir not in target.parents and target != workdir:
        raise PermissionError(f"Refused: {relpath} escapes the workspace")
    return target


@tool
def read_file(path: str, line_start: int = 1, line_end: int = 0) -> str:
    """Read a UTF-8 text file from the agent's workspace.

    Args:
        path: Relative path inside the workspace, e.g. "notes.md".
        line_start: 1-indexed first line to include (default 1 = top of file).
        line_end: 1-indexed last line to include, INCLUSIVE.
                  0 (default) means "to end of file".
                  Useful for sampling big files without dragging the whole
                  thing into context.
    """
    try:
        p = _resolve(path)
        if not p.exists():
            return f"File not found: {path}"
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        n = len(lines)
        s = max(1, line_start) - 1
        e = n if line_end <= 0 else min(line_end, n)
        slc = "".join(lines[s:e])
        if s > 0 or e < n:
            return f"[lines {s + 1}-{e} of {n}]\n{slc}"
        return slc
    except Exception as e:
        return f"read_file error: {e!r}"


@tool
def grep_in_files(pattern: str, glob: str = "**/*", max_hits: int = 50) -> str:
    """Search for a regex pattern across files in the agent's workspace.
    Returns up to `max_hits` matching lines, each prefixed with `path:line: `.

    Args:
        pattern: A Python regular-expression pattern.
        glob: Glob restricting which files to search (default: every file).
        max_hits: Hard cap on returned lines.
    """
    import re

    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"bad regex: {e}"
    try:
        workdir = get_settings().workdir
        out: list[str] = []
        for p in sorted(workdir.glob(glob)):
            if not p.is_file():
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
                    if rx.search(line):
                        rel = p.relative_to(workdir)
                        out.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(out) >= max_hits:
                            return "\n".join(out) + f"\n[capped at {max_hits} hits]"
            except (UnicodeDecodeError, OSError):
                continue
        return "\n".join(out) if out else "(no matches)"
    except Exception as e:
        return f"grep_in_files error: {e!r}"


@tool
def write_file(path: str, content: str) -> str:
    """Write (or overwrite) a UTF-8 text file in the agent's workspace.

    Args:
        path: Relative path inside the workspace.
        content: Full text to write.
    """
    try:
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"write_file error: {e!r}"


@tool
def list_files() -> str:
    """List all files currently in the agent's workspace."""
    try:
        workdir = get_settings().workdir
        rows = []
        for p in sorted(workdir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(workdir)
                rows.append(f"{rel}  ({p.stat().st_size} bytes)")
        return "\n".join(rows) if rows else "(workspace is empty)"
    except Exception as e:
        return f"list_files error: {e!r}"
