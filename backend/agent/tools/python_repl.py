"""Sandboxed Python REPL — runs in a child subprocess with a hard timeout.

Production agents would use E2B or a microVM here; for the demo a subprocess
is enough to keep the agent from corrupting the host process state."""
from __future__ import annotations

import subprocess
import sys
import textwrap

from langchain_core.tools import tool

TIMEOUT_S = 8
MAX_OUTPUT = 4_000


@tool
def python_repl(code: str) -> str:
    """Run a short Python snippet in an isolated subprocess (8s timeout).
    Use for data manipulation, regex testing, quick algorithms — anything
    you'd open `python -c` for. Print results explicitly; only stdout is captured.

    Args:
        code: Python source. Use `print(...)` to surface values.
    """
    wrapped = textwrap.dedent(code)
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", wrapped],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return f"python_repl timed out after {TIMEOUT_S}s"
    except Exception as e:
        return f"python_repl crashed: {e!r}"

    parts = []
    if result.stdout:
        parts.append("[stdout]\n" + result.stdout[:MAX_OUTPUT])
    if result.stderr:
        parts.append("[stderr]\n" + result.stderr[:MAX_OUTPUT])
    if result.returncode != 0:
        parts.append(f"[exit code] {result.returncode}")
    return "\n\n".join(parts) or "(no output)"
