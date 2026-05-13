"""Per-tool permission gate · Claude-Code-style (always-ask / auto-approve / deny).

Three scopes, in increasing precedence:
  - DEFAULT_POLICY (hard-coded · safest fallback)
  - global file: `~/.taotao/permissions.json`           (cross-project)
  - project file: `<repo>/.taotao/permissions.json`     (per-checkout)

A *policy* maps a tool-name pattern to one of:
  - "allow"     · execute without asking
  - "ask"       · raise PermissionRequired and let the front-end prompt user
  - "deny"      · refuse · surface a clean error to the LLM

Pattern matching is glob-style (`fnmatch`): e.g. `file.write`, `mcp__*`,
`bash:*rm*`. First match wins (project > global > default).

To keep this dependency-light it's pure stdlib JSON · no pydantic schema.

Wire-up: `agent/tools/safe_exec.py::safe_run_tool` calls `gate(name, args)`
before invocation; the harness (and graph executor) treat
`PermissionRequired` as a non-fatal signal and emit a `permission_request`
trace event. The CLI / web UI can persist the user's decision via
`add_rule(...)` so the question doesn't repeat next turn.
"""
from __future__ import annotations

import fnmatch
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

Decision = Literal["allow", "ask", "deny"]


# Tools that touch the user's machine in irreversible ways → ask by default.
# Read-only tools (web_search / calculator / list_files / read_file) → allow.
DEFAULT_POLICY: dict[str, Decision] = {
    # destructive · always ask
    "write_file":      "ask",
    "delete_file":     "ask",
    "python_repl":     "ask",
    "bash":            "ask",
    "shell":           "ask",
    # MCP tools come from third-party servers · default ask (least-trust)
    "mcp__*":          "ask",
    # safe defaults
    "read_file":       "allow",
    "list_files":      "allow",
    "calculator":      "allow",
    "current_time":    "allow",
    "web_search":      "allow",
    "remember":        "allow",
    "recall":          "allow",
    "load_skill":      "allow",
    "set_profile":     "allow",
    "get_profile":     "allow",
    "dispatch_subagent": "allow",
    "final_answer":    "allow",
    # everything else
    "*":               "allow",
}


@dataclass
class Rule:
    pattern: str
    decision: Decision
    note: str = ""


_LOCK = threading.Lock()
_RUNTIME_RULES: list[Rule] = []  # added by add_rule() · session-scope


def _global_path() -> Path:
    return Path.home() / ".taotao" / "permissions.json"


def _project_path() -> Path | None:
    cwd = Path.cwd()
    for p in [cwd, *cwd.parents]:
        cand = p / ".taotao" / "permissions.json"
        if cand.exists():
            return cand
    return None


def _read_rules(path: Path | None) -> list[Rule]:
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rules: list[Rule] = []
    for item in data.get("rules", []) or []:
        try:
            rules.append(Rule(
                pattern=item["pattern"],
                decision=item.get("decision", "ask"),
                note=item.get("note", ""),
            ))
        except KeyError:
            continue
    return rules


def _all_rules() -> list[Rule]:
    """Project > global > runtime > default (first match wins)."""
    out: list[Rule] = []
    out += _read_rules(_project_path())
    out += _read_rules(_global_path())
    with _LOCK:
        out += list(_RUNTIME_RULES)
    for pat, dec in DEFAULT_POLICY.items():
        out.append(Rule(pattern=pat, decision=dec, note="default"))
    return out


def _decide(tool_name: str) -> Decision:
    for r in _all_rules():
        if fnmatch.fnmatchcase(tool_name, r.pattern):
            return r.decision
    return "ask"


# ------------------------------------------------------- public surface


class PermissionRequired(Exception):
    """Raised by `gate()` when the policy says 'ask'.  The harness catches
    this, emits a trace event, and lets the UI prompt the user.  The user's
    answer is then stored via `add_rule()` so subsequent turns don't ask
    again (until session reset / process restart)."""

    def __init__(self, tool_name: str, args: dict | str):
        self.tool_name = tool_name
        self.args = args
        super().__init__(f"permission required for tool {tool_name!r}")


def gate(tool_name: str, args: dict | str) -> None:
    """Raise PermissionRequired if policy is 'ask' · raise PermissionError
    if 'deny' · return cleanly if 'allow'."""
    decision = _decide(tool_name)
    if decision == "allow":
        return
    if decision == "deny":
        raise PermissionError(f"tool {tool_name!r} is denied by policy")
    raise PermissionRequired(tool_name, args)


def add_rule(pattern: str, decision: Decision, *, persist: str = "session", note: str = "") -> Rule:
    """Add a rule.  `persist`:
      - "session" : in-memory only (default · safest)
      - "global"  : also append to `~/.taotao/permissions.json`
      - "project" : also append to `<cwd-or-parent>/.taotao/permissions.json`
    """
    if decision not in ("allow", "ask", "deny"):
        raise ValueError(f"bad decision {decision!r}")
    rule = Rule(pattern=pattern, decision=decision, note=note)
    with _LOCK:
        _RUNTIME_RULES.insert(0, rule)
    if persist in ("global", "project"):
        path = _global_path() if persist == "global" else (_project_path() or (Path.cwd() / ".taotao" / "permissions.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"rules": []}
        except Exception:
            existing = {"rules": []}
        existing.setdefault("rules", []).insert(0, {
            "pattern": pattern, "decision": decision, "note": note,
        })
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return rule


def list_rules() -> list[dict]:
    """Snapshot for diagnostics / UI."""
    return [
        {"pattern": r.pattern, "decision": r.decision, "note": r.note}
        for r in _all_rules()
    ]


def reset_runtime_rules() -> None:
    with _LOCK:
        _RUNTIME_RULES.clear()
