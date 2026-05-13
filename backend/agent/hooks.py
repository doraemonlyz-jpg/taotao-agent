"""User hooks · Claude-Code-style `hooks.json` · 5 events.

Hooks let advanced users plug shell commands (lint / fmt / git status /
notify / log) into the agent's life-cycle without touching python code.

Events fired (in order, per turn):

  - `session_start`      · before the loop starts, once per /chat call
  - `pre_tool_use`       · before each tool call
  - `post_tool_use`      · after each tool call (regardless of success)
  - `notification`       · arbitrary milestone (e.g. compaction done, budget warning)
  - `stop`               · session ended (final answer or error)

Config file (first found wins, project > global):
  - `<cwd-or-parent>/.taotao/hooks.json`
  - `~/.taotao/hooks.json`

Schema:
  {
    "hooks": {
      "pre_tool_use": [
        {"match": "write_file|delete_file", "command": "git status -s"},
        {"match": "*",                     "command": "echo $TAOTAO_TOOL >> .taotao/audit.log"}
      ],
      "stop": [
        {"command": "terminal-notifier -title 桃桃 -message done"}
      ]
    }
  }

Environment variables passed to every hook:
  TAOTAO_EVENT, TAOTAO_SESSION_ID, TAOTAO_TOOL, TAOTAO_ARGS_JSON,
  TAOTAO_RESULT_PREVIEW (post_tool_use only · first 240 chars)

Hooks are run with subprocess.run() · `timeout=5s` hard-coded · stdout
captured into the trace event but NEVER forwarded to the LLM.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Any, Iterable

from .observability import emit


HOOK_EVENTS = ("session_start", "pre_tool_use", "post_tool_use", "notification", "stop")
HOOK_TIMEOUT_S = 5.0

_LOCK = threading.Lock()


def _global_path() -> Path:
    return Path.home() / ".taotao" / "hooks.json"


def _project_path() -> Path | None:
    cwd = Path.cwd()
    for p in [cwd, *cwd.parents]:
        cand = p / ".taotao" / "hooks.json"
        if cand.exists():
            return cand
    return None


def _load_config() -> dict[str, list[dict]]:
    """Merge project + global · project entries take precedence."""
    out: dict[str, list[dict]] = {ev: [] for ev in HOOK_EVENTS}
    for path in (_project_path(), _global_path()):
        if not path or not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ev, entries in (data.get("hooks") or {}).items():
            if ev not in HOOK_EVENTS or not isinstance(entries, list):
                continue
            for e in entries:
                if isinstance(e, dict) and "command" in e:
                    out[ev].append(e)
    return out


def _matches(pattern: str | None, tool_name: str | None) -> bool:
    if not pattern:
        return True
    try:
        return bool(re.fullmatch(pattern, tool_name or ""))
    except re.error:
        # not a regex · treat as literal
        return pattern == tool_name


def fire(event: str, *, session_id: str = "", tool_name: str = "",
         args: Any = None, result_preview: str = "") -> None:
    """Fire all hooks registered for `event`.  Never raises; failures are
    logged as trace events so the agent loop is unaffected."""
    if event not in HOOK_EVENTS:
        return
    config = _load_config()
    entries = config.get(event) or []
    if not entries:
        return

    env = {
        **os.environ,
        "TAOTAO_EVENT": event,
        "TAOTAO_SESSION_ID": session_id,
        "TAOTAO_TOOL": tool_name,
        "TAOTAO_ARGS_JSON": json.dumps(args, default=str)[:2000] if args is not None else "",
        "TAOTAO_RESULT_PREVIEW": result_preview[:240],
    }

    with _LOCK:
        for entry in entries:
            if not _matches(entry.get("match"), tool_name):
                continue
            cmd = entry["command"]
            try:
                proc = subprocess.run(
                    cmd if isinstance(cmd, list) else shlex.split(cmd),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=HOOK_TIMEOUT_S,
                )
                emit("hook", event, {
                    "match": entry.get("match"), "ok": proc.returncode == 0,
                    "stdout": proc.stdout[:240], "stderr": proc.stderr[:240],
                    "rc": proc.returncode, "tool": tool_name,
                }, session_id=session_id)
            except Exception as e:
                emit("hook", event, {
                    "match": entry.get("match"), "ok": False, "error": repr(e),
                    "tool": tool_name,
                }, session_id=session_id)


def list_hooks() -> dict[str, list[dict]]:
    return _load_config()
