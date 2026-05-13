"""Slash-commands · a thin command palette layer over the chat endpoint.

When a user message starts with `/` the backend tries to handle it locally
without burning an LLM call.  Two flavours:

  1. Built-ins · python functions in BUILTINS dict ─── /clear /compact
     /cost /model /help /hooks /perms /skills

  2. Custom · markdown files in `data/commands/<name>.md` whose body
     becomes the prompt that gets sent to the LLM (with $ARG substitution).
     This mirrors Claude Code's `~/.claude/commands/*.md` system.

Return value of `dispatch(text, sid)`:
  - None         → not a slash command, fall through to normal flow
  - str          → reply directly to the user (no LLM call)
  - dict("rewrite": "...")  → run the LLM with this rewritten prompt instead

Everything here is sync; the chat endpoint awaits the result via
`asyncio.to_thread`.
"""
from __future__ import annotations

import json
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from .config import get_settings
from .harness.persistence import get_store as get_harness_store
from .harness.compaction import compact, estimate_tokens
from .observability import usage as usage_tracker
from .observability.tracer import emit
from .permissions import list_rules, add_rule
from .hooks import list_hooks
from .memory import list_skills


# ---------------------------------------------------------------- built-ins


def _cmd_help(sid: str, _arg: str) -> str:
    cmds = sorted(BUILTINS.keys()) + sorted(_custom_command_names())
    return (
        "Available slash commands:\n"
        + "\n".join(f"  /{c}" for c in cmds)
        + "\n\nUse /<name> to run a built-in, or /custom-name [arg] to run a "
        "markdown command from data/commands/."
    )


def _cmd_clear(sid: str, _arg: str) -> str:
    get_harness_store().clear(sid)
    emit("slash", "clear", {"session": sid}, session_id=sid)
    return f"✓ session {sid[:8]}… cleared (next message starts fresh)."


def _cmd_compact(sid: str, _arg: str) -> str:
    store = get_harness_store()
    msgs = store.load(sid)
    if not msgs:
        return "(no messages yet · nothing to compact)"
    before = estimate_tokens(msgs)
    new = compact(msgs)
    store.save(sid, new)
    after = estimate_tokens(new)
    emit("slash", "compact", {"before": before, "after": after}, session_id=sid)
    return f"✓ compacted · {before} → {after} tokens (saved {before - after})."


def _cmd_cost(sid: str, _arg: str) -> str:
    snap = usage_tracker.snapshot(sid)
    g = snap.get("global", {})
    s = snap.get("session", {})
    return (
        f"💰 cost report\n"
        f"  session  · ${s.get('cost_usd', 0):.4f}  "
        f"({s.get('tokens', 0)} tokens · {s.get('calls', 0)} calls)\n"
        f"  global   · ${g.get('cost_usd', 0):.4f}  "
        f"({g.get('tokens', 0)} tokens · {g.get('calls', 0)} calls)\n"
        f"  budget   · ${get_settings().session_budget_usd:.2f} per session"
    )


def _cmd_model(sid: str, arg: str) -> str | dict:
    cfg = get_settings()
    if not arg.strip():
        return f"current · model={cfg.model} · fast={cfg.fast_model}\nuse /model <id>  to switch."
    return {
        "rewrite": None,
        "side_effect": ("model_switch", {"model": arg.strip()}),
        "reply": f"⚙️  request model switch → {arg.strip()} (call POST /model to apply).",
    }


def _cmd_skills(sid: str, _arg: str) -> str:
    items = list_skills()
    if not items:
        return "(no skills loaded · drop md files into data/skills/)"
    return "skills:\n" + "\n".join(f"  - {s.name}: {s.description}" for s in items)


def _cmd_perms(sid: str, _arg: str) -> str:
    rules = list_rules()
    return "permission rules (top match wins):\n" + "\n".join(
        f"  {r['pattern']:<28} → {r['decision']:<5}  {r['note']}" for r in rules[:30]
    )


def _cmd_hooks(sid: str, _arg: str) -> str:
    cfg = list_hooks()
    out = ["registered hooks:"]
    for ev, items in cfg.items():
        if not items:
            continue
        out.append(f"  [{ev}]")
        for h in items:
            out.append(f"    match={h.get('match', '*')}  command={h.get('command')}")
    if len(out) == 1:
        return "(no hooks · drop a .taotao/hooks.json in cwd or ~)"
    return "\n".join(out)


BUILTINS: dict[str, Callable[[str, str], Any]] = {
    "help":    _cmd_help,
    "clear":   _cmd_clear,
    "compact": _cmd_compact,
    "cost":    _cmd_cost,
    "model":   _cmd_model,
    "skills":  _cmd_skills,
    "perms":   _cmd_perms,
    "hooks":   _cmd_hooks,
}


# ---------------------------------------------------------------- custom .md commands


def _commands_dir() -> Path:
    cfg = get_settings()
    p = cfg.workdir.parent / "commands"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _custom_command_names() -> list[str]:
    return sorted(p.stem for p in _commands_dir().glob("*.md"))


def _load_custom(name: str) -> str | None:
    p = _commands_dir() / f"{name}.md"
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


# ---------------------------------------------------------------- entry point


def dispatch(message: str, sid: str) -> Any | None:
    """Return None if `message` is not a slash command, else a str / dict."""
    msg = message.strip()
    if not msg.startswith("/"):
        return None
    parts = shlex.split(msg[1:]) if " " in msg[1:] else [msg[1:].strip(), ""]
    if len(parts) == 1:
        parts.append("")
    name, *rest = parts
    arg = " ".join(rest).strip()

    if name in BUILTINS:
        return BUILTINS[name](sid, arg)

    body = _load_custom(name)
    if body is not None:
        # Custom command · substitute $ARG / $ARGS, return a rewrite.
        rewritten = body.replace("$ARG", arg).replace("$ARGS", arg)
        return {"rewrite": rewritten}

    return f"unknown command /{name}.  try /help."
