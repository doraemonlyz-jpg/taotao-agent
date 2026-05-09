"""Optional component — Guardrails.

Two layers:
- input_guardrail: refuse obvious prompt-injection / policy violations
- output_guardrail: redact PII patterns before sending to the user

Production-grade work would use llm-guard, NeMo Guardrails, or Lakera.
Kept dependency-free here so the demo runs anywhere."""
from __future__ import annotations

import re

from ..config import get_settings
from ..observability import emit, usage as usage_tracker
from ..state import AgentState

# crude but useful: lines that *look* like prompt-injection or system overrides
_INJECTION = re.compile(
    r"(ignore (all |the )?(previous|prior) (instructions|prompts))|"
    r"(disregard (the )?system prompt)|"
    r"(reveal (your |the )?system prompt)|"
    r"(\bjailbreak\b)",
    re.I,
)

# PII patterns we redact on the way OUT.
# Phone regex requires explicit separators so we don't false-positive on
# long math outputs like 65535.999992370605.
_EMAIL = re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b")
_PHONE = re.compile(
    r"(?:(?<=\D)|^)"  # not preceded by another digit
    r"\+?\d{1,3}[\s\-.]\d{3,4}[\s\-.]\d{4,5}"  # +1 555-123-4567
    r"|"
    r"\(\d{3}\)\s?\d{3}[\s\-.]?\d{4}"  # (555) 123-4567
    r"|"
    r"\b1[3-9]\d[\s\-.]?\d{4}[\s\-.]?\d{4}\b"  # CN mobile: 138-1234-5678
)


def input_guardrail(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    text = state.get("user_input", "")

    # 1) Prompt-injection check
    if _INJECTION.search(text):
        emit("guardrail_in", "guardrail",
             {"action": "block", "reason": "prompt_injection"},
             session_id=sid)
        return {
            "blocked": True,
            "block_reason": "Refused: input matches a known prompt-injection pattern.",
            "final_answer": "I can't follow that request — it looks like a prompt-injection attempt.",
        }

    # 2) Per-session cost cap — refuse before spending any more tokens
    cfg = get_settings()
    if cfg.session_budget_usd > 0 and sid:
        spent = usage_tracker.session_cost(sid)
        if spent >= cfg.session_budget_usd:
            emit("guardrail_in", "guardrail",
                 {"action": "block", "reason": "budget_exceeded",
                  "spent_usd": round(spent, 4), "budget_usd": cfg.session_budget_usd},
                 session_id=sid)
            return {
                "blocked": True,
                "block_reason": f"Session budget of ${cfg.session_budget_usd:.2f} reached.",
                "final_answer": (
                    f"💸 Session budget of ${cfg.session_budget_usd:.2f} reached "
                    f"(spent ${spent:.4f}). Start a new session or raise "
                    f"AGENT_SESSION_BUDGET_USD."
                ),
            }

    emit("guardrail_in", "guardrail", {"action": "pass"}, session_id=sid)
    return {"blocked": False}


def output_guardrail(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    raw = state.get("final_answer", "") or ""

    # Coerce to plain text in case anyone upstream stuffed in a content-block list
    if isinstance(raw, list):
        from .executor import _to_text  # local import to avoid cycles
        answer = _to_text(raw)
    else:
        answer = str(raw)

    redacted = _EMAIL.sub("[email-redacted]", answer)
    redacted = _PHONE.sub("[phone-redacted]", redacted)

    if redacted != answer:
        emit("guardrail_out", "guardrail",
             {"action": "redact", "pii": True},
             session_id=sid)
    else:
        emit("guardrail_out", "guardrail", {"action": "pass"}, session_id=sid)

    # Canonical final answer event — frontend listens for this.
    emit("output_guardrail", "answer", {"text": redacted}, session_id=sid)

    return {"final_answer": redacted}
