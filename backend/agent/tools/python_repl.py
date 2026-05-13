"""Sandboxed Python REPL · tool for the agent to execute short snippets.

Backend selection is env-driven · see `agent.tools._sandbox`:
  - PYTHON_REPL_SANDBOX=subprocess  (default · fast · NOT for hostile input)
  - PYTHON_REPL_SANDBOX=docker      (production · network-off container)
  - PYTHON_REPL_SANDBOX=gvisor      (paranoid · gVisor-isolated container)

For public SaaS deployments use docker or gvisor · the LLM WILL be
prompt-injected eventually and the subprocess backend is not designed
to contain that.
"""
from __future__ import annotations

from langchain_core.tools import tool

from ._sandbox import run_python

TIMEOUT_S = 8
MAX_OUTPUT = 4_000


@tool
def python_repl(code: str) -> str:
    """Run a short Python snippet in an isolated sandbox (8s timeout).
    Use for data manipulation, regex testing, quick algorithms · anything
    you'd open `python -c` for. Print results explicitly; only stdout
    is captured.

    Args:
        code: Python source. Use `print(...)` to surface values.
    """
    result = run_python(code, timeout_s=TIMEOUT_S)
    if result.timed_out:
        return f"python_repl timed out after {TIMEOUT_S}s (backend={result.backend})"

    parts = []
    if result.stdout:
        parts.append("[stdout]\n" + result.stdout[:MAX_OUTPUT])
    if result.stderr:
        parts.append("[stderr]\n" + result.stderr[:MAX_OUTPUT])
    if result.exit_code != 0:
        parts.append(f"[exit code] {result.exit_code} (backend={result.backend})")
    return "\n\n".join(parts) or "(no output)"
