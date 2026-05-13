"""`taotao` CLI · streams /chat/v2 to your terminal · session-aware.

Usage examples:

    python taotao_cli.py "find me 3 papers on speculative decoding"
    python taotao_cli.py --continue "and now summarise paper #2"
    python taotao_cli.py --resume <session-id>
    python taotao_cli.py --list-sessions
    python taotao_cli.py --plan "refactor the harness loop"
    python taotao_cli.py /cost
    python taotao_cli.py /clear --continue

Default backend: `http://localhost:8000`.  Override with `TAOTAO_API` env.

`--continue` re-uses the most recent session id from `~/.taotao/last_session`.
`--resume <id>` jumps to a specific session_id, including ones from past
process lifetimes (we read the trace JSONL via `/chat/replay/sessions`).

Designed to be tiny + dependency-light · only stdlib + httpx + sseclient
(both already in our backend requirements).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx


API = os.environ.get("TAOTAO_API", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("TAOTAO_API_KEY") or os.environ.get("AGENT_API_KEY")
LAST = Path.home() / ".taotao" / "last_session"


def _last_session() -> str | None:
    if LAST.exists():
        try:
            sid = LAST.read_text(encoding="utf-8").strip()
            return sid or None
        except OSError:
            return None
    return None


def _save_last(sid: str) -> None:
    LAST.parent.mkdir(parents=True, exist_ok=True)
    LAST.write_text(sid, encoding="utf-8")


def _headers() -> dict:
    h = {"Accept": "text/event-stream"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def list_sessions(limit: int = 20) -> None:
    r = httpx.get(f"{API}/chat/replay/sessions", params={"limit": limit}, timeout=10)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        print("(no sessions in trace log)")
        return
    print(f"{'session_id':<38}  {'first_user':<70}")
    print("-" * 110)
    for row in rows:
        sid = row["session_id"]
        first = (row.get("first_user") or "(no user input)")[:70]
        print(f"{sid:<38}  {first}")


def stream_chat(message: str, session_id: str | None, *, plan: bool = False) -> str:
    """Send `message` to /chat/v2 (SSE) · print tokens live · return final sid."""
    payload = {"message": message, "mode": "plan" if plan else "act"}
    if session_id:
        payload["session_id"] = session_id

    sid_out = session_id or "unknown"
    last_token_was_newline = True
    with httpx.stream(
        "POST", f"{API}/chat/v2", json=payload, headers=_headers(), timeout=600,
    ) as r:
        r.raise_for_status()
        event_name = "message"
        for raw in r.iter_lines():
            if not raw:
                event_name = "message"
                continue
            line = raw if isinstance(raw, str) else raw.decode("utf-8")
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                if event_name == "session":
                    sid_out = obj.get("session_id") or sid_out
                    print(f"[session {sid_out[:8]}...{' · plan-mode' if plan else ''}]")
                elif event_name == "token":
                    sys.stdout.write(obj.get("text", ""))
                    sys.stdout.flush()
                    last_token_was_newline = obj.get("text", "").endswith("\n")
                elif event_name == "trace":
                    kind = obj.get("kind", "")
                    if kind == "answer":
                        # answer arrives at end · already streamed via tokens
                        pass
                    elif kind == "tool_call":
                        names = ", ".join(c["name"] for c in obj.get("payload", {}).get("calls", []))
                        if not last_token_was_newline:
                            print()
                        print(f"  ← {names}")
                        last_token_was_newline = True
                    elif kind == "permission_request":
                        p = obj.get("payload", {})
                        print(f"\n  ⚠ permission needed for tool {p.get('tool')!r} · run `/perms` to manage")
                        last_token_was_newline = True
                    elif kind == "notification":
                        print(f"\n  🔔 {obj.get('payload', {}).get('message', '')}")
                        last_token_was_newline = True
                elif event_name == "done":
                    if not last_token_was_newline:
                        print()
                    return sid_out
    return sid_out


def main() -> None:
    ap = argparse.ArgumentParser(description="taotao agent CLI")
    ap.add_argument("message", nargs="*", help="message to send · or a /slash command")
    ap.add_argument("--continue", dest="cont", action="store_true",
                    help="reuse last session id")
    ap.add_argument("--resume", dest="resume", metavar="SID",
                    help="resume a specific session id")
    ap.add_argument("--plan", action="store_true",
                    help="run in PLAN MODE · destructive tools blocked")
    ap.add_argument("--list-sessions", action="store_true",
                    help="show recent sessions found in the trace log")
    args = ap.parse_args()

    if args.list_sessions:
        list_sessions()
        return

    msg = " ".join(args.message).strip()
    if not msg:
        sys.exit("usage: taotao_cli.py [--continue|--resume SID] [--plan] <message>")

    sid: str | None = None
    if args.resume:
        sid = args.resume.strip()
    elif args.cont:
        sid = _last_session()
        if not sid:
            print("(no previous session · starting fresh)")

    final_sid = stream_chat(msg, sid, plan=args.plan)
    if final_sid:
        _save_last(final_sid)


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as e:
        sys.exit(f"http error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
