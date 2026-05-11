"""Context compaction · the harness equivalent of the graph's `summarizer`
node — but triggered by token budget, not by step count.

# Why a different trigger?

The graph's `summarizer` runs every turn that crosses MAX_MESSAGES = 30.
That's a fine heuristic but it ignores reality:
  - 30 short user pings ≈ 600 tokens  → safe, no need to compact
  - 30 messages with one giant tool result each ≈ 80k tokens → urgent

Token-aware compaction is a cleaner contract: "stay below X tokens".
That's also what every production harness (Claude Code, Cursor) actually
does, because the LIMIT they're worried about is the model's context window
+ cost-per-call, both of which are tokens not turns.

# Strategy

Keep the SYSTEM prompt + the most recent N "important" messages verbatim
(important = user message, the last assistant reply, and the few tool
results that produced it).  Replace everything older with a single
SystemMessage like:

    [Conversation summary so far]
    User asked X.  We searched for Y, found Z.  Saved memory M.
    User then asked Q · we answered with R using tool T.

So a 200-message → 80k-token window collapses to ~5 messages → ~10k tokens
without losing meaning.

# Why this is safer than naive `messages[-N:]` truncation

Truncation breaks tool_call / tool_result pairing (the assistant message
saying "I'm calling tool X" gets dropped, but the ToolMessage staying
behind references a missing tool_call_id, and the model freaks out).
Summarisation produces a clean SystemMessage that has no dangling refs.
"""
from __future__ import annotations

from typing import Iterable

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..nodes.llm import get_fast_llm

# Conservative defaults · tuned for Claude Sonnet 4 / GPT-4o-class context.
# Override via env if you're on a 1M-context model and don't want to compact.
DEFAULT_TOKEN_BUDGET = 60_000   # start summarising once we cross this
DEFAULT_KEEP_RECENT = 6         # keep last N messages verbatim


def estimate_tokens(messages: Iterable[BaseMessage]) -> int:
    """Cheap-and-correct-enough token estimator: 1 token ≈ 4 chars.

    For real cost decisions you'd use tiktoken / anthropic.count_tokens,
    but those add ~20ms per call and we run this on EVERY loop step.
    A 4× heuristic over-estimates slightly which is the side we want
    (safer to compact too early than too late).
    """
    total = 0
    for m in messages:
        c = m.content
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            # Anthropic-style list of blocks
            for b in c:
                if isinstance(b, dict):
                    total += len(b.get("text", "")) + len(str(b.get("input", "")))
                else:
                    total += len(str(b))
        # tool_calls live on the AIMessage too
        for tc in getattr(m, "tool_calls", None) or []:
            total += len(str(tc.get("args", "")))
    return total // 4


COMPACTION_PROMPT = """\
Summarise the conversation below in 6-12 bullets. Keep:
  - Concrete facts the user told us (names, dates, preferences, project ctx)
  - Decisions made, in order
  - Tool results that influenced an answer (one bullet per major result)
  - Any unresolved questions

DROP:
  - Politeness / chit-chat
  - Verbose tool output (paraphrase: "searched X, top result was Y")
  - Reasoning that didn't change the trajectory

Output bullet form only · no preamble · no closing.

Conversation:
"""


def _msg_to_text(m: BaseMessage) -> str:
    role = type(m).__name__.replace("Message", "").lower() or "?"
    c = m.content if isinstance(m.content, str) else str(m.content)
    # Truncate any single message to keep the summariser prompt itself sane.
    if len(c) > 2000:
        c = c[:1000] + "  …(truncated)…  " + c[-500:]
    return f"[{role}] {c}"


def compact(
    messages: list[AnyMessage],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> list[AnyMessage]:
    """Synchronous compactor · returns a NEW list (does not mutate).

    Layout returned:

        [SystemMessage(original system prompt, if any),
         SystemMessage("[Summary so far] ..."),
         <last `keep_recent` messages verbatim, with tool-call pairing fixed>]

    Edge cases handled:
      - if there are no SystemMessages, we still emit the summary one
      - if `keep_recent` slices off an orphan ToolMessage (one whose
        AIMessage with the matching tool_call is now in the summary),
        we drop that ToolMessage rather than ship a dangling reference
    """
    if len(messages) <= keep_recent + 2:
        return list(messages)

    # Pull system messages aside · they're cheap and identity-defining.
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    body = [m for m in messages if not isinstance(m, SystemMessage)]

    if len(body) <= keep_recent:
        return list(messages)

    older = body[:-keep_recent]
    recent = body[-keep_recent:]

    # 1. Summarise older with the cheap model (fast_model)
    transcript = "\n\n".join(_msg_to_text(m) for m in older)
    try:
        resp = get_fast_llm(temperature=0).invoke(COMPACTION_PROMPT + transcript)
        summary_text = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        # If summarisation fails, fall back to a brutal truncation note —
        # better to lose context than lose the whole conversation.
        summary_text = f"[Summary unavailable · prior {len(older)} messages dropped due to error: {e!r}]"

    summary_msg = SystemMessage(content=f"[Conversation summary]\n{summary_text}")

    # 2. Drop orphan ToolMessages from `recent` — those whose tool_call_id
    # references a now-summarised AIMessage.
    valid_call_ids: set[str] = set()
    for m in recent:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                if tc.get("id"):
                    valid_call_ids.add(tc["id"])
    cleaned_recent: list[AnyMessage] = []
    for m in recent:
        if isinstance(m, ToolMessage) and m.tool_call_id not in valid_call_ids:
            continue  # orphan · drop
        cleaned_recent.append(m)

    return [*system_msgs, summary_msg, *cleaned_recent]


def needs_compaction(messages: list[AnyMessage], budget: int = DEFAULT_TOKEN_BUDGET) -> bool:
    return estimate_tokens(messages) > budget
