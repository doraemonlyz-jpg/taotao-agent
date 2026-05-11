"""The harness · one async function · one while-loop · ~150 lines.

THIS IS THE WHOLE AGENT.  Read it top-to-bottom and you'll understand every
control-flow decision in the system.

Compared to graph version (`agent/graph.py` + 13 node files = 1164 lines),
the entire control flow is below.  Every decision the graph encodes in
edges, the harness encodes in either:
  - the LLM's choice of tool (or no tool)            ← runtime, by the model
  - a hand-coded check in this loop                  ← when safety demands it

Layered structure:

    user_msg
        │
        ▼
    [input_guardrail]            ← reuse from agent/nodes/guardrails.py
        │
        ▼
    while step < MAX_STEPS:
        ▸ if needs_compaction(messages): messages = compact(messages)
        ▸ stream LLM response (token events emit live)
        ▸ append response to messages
        ▸ persist messages (crash-safe)
        ▸ if no tool_calls and no final_answer: break  ← done
        ▸ if final_answer called: break                 ← done
        ▸ for each tool_call: run safe_run_tool, append ToolMessage, persist
        ▸ step += 1
        │
        ▼
    [output_guardrail]           ← reuse PII redaction
        │
        ▼
    final answer
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..config import get_settings
from ..memory import get_profile
from ..nodes.guardrails import _EMAIL, _INJECTION, _PHONE
from ..nodes.llm import get_llm
from ..observability import emit, usage as usage_tracker
from ..tools.safe_exec import safe_run_tool

from .compaction import needs_compaction, compact, estimate_tokens
from .persistence import HarnessSessionStore, get_store
from .prompt import render_system_prompt
from .tools import HARNESS_TOOLS, tool_descriptions

# Hard cap · prevents runaway loops.  In practice well-tuned harnesses
# rarely exceed 6-8 steps for normal queries; 20 is a generous ceiling.
MAX_STEPS = 20


# ─────────────────────────────────────────────── public API


class Harness:
    """A reusable harness instance · holds the persisted store + tool list.

    One per process is fine (no per-call state) · we cache it as a module
    singleton in `__init__.py`'s `run_harness`.
    """

    def __init__(self) -> None:
        self.store: HarnessSessionStore = get_store()
        self.tools = HARNESS_TOOLS
        self.tools_by_name = {t.name: t for t in self.tools}

    # ─────────────────────────────────────────── helpers (inputs / outputs)

    def _input_guardrail(self, text: str, session_id: str) -> str | None:
        """Return None if OK · else return the canned refusal string."""
        if _INJECTION.search(text):
            emit("guardrail_in", "guardrail",
                 {"action": "block", "reason": "prompt_injection"},
                 session_id=session_id)
            return "I can't follow that request — it looks like a prompt-injection attempt."

        cfg = get_settings()
        if cfg.session_budget_usd > 0 and session_id:
            spent = usage_tracker.session_cost(session_id)
            if spent >= cfg.session_budget_usd:
                emit("guardrail_in", "guardrail",
                     {"action": "block", "reason": "budget_exceeded",
                      "spent_usd": round(spent, 4), "budget_usd": cfg.session_budget_usd},
                     session_id=session_id)
                return (f"💸 Session budget of ${cfg.session_budget_usd:.2f} reached "
                        f"(spent ${spent:.4f}).")
        emit("guardrail_in", "guardrail", {"action": "pass"}, session_id=session_id)
        return None

    def _output_guardrail(self, answer: str, session_id: str) -> str:
        """PII-redact the final answer · same patterns as the graph version."""
        redacted = _EMAIL.sub("[email-redacted]", answer)
        redacted = _PHONE.sub("[phone-redacted]", redacted)
        emit("guardrail_out", "guardrail",
             {"action": "redact" if redacted != answer else "pass"},
             session_id=session_id)
        emit("output_guardrail", "answer", {"text": redacted}, session_id=session_id)
        return redacted

    def _profile_blob(self) -> str | None:
        """Render the user profile dict as a tiny prompt block · or None."""
        try:
            data = get_profile().all()
        except Exception:
            return None
        if not data:
            return None
        return "\n".join(f"- {k}: {v}" for k, v in data.items())

    # ─────────────────────────────────────────── the loop

    async def run(
        self,
        user_input: str,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """Drive one turn of the harness · yields trace events; the SSE
        layer in app.py forwards them to the client.

        Each yielded dict has the same shape as the graph's emit() events:
            {"node": str, "kind": str, "payload": {...}}

        The frontend's existing SSE handler is therefore reused unchanged.
        """
        sid = session_id
        emit("harness", "perception", {"text": user_input[:240]}, session_id=sid)

        # ---- 1. input guardrail -----------------------------------------
        refusal = self._input_guardrail(user_input, sid)
        if refusal:
            redacted = self._output_guardrail(refusal, sid)
            yield {"node": "harness", "kind": "answer", "payload": {"text": redacted}}
            return

        # ---- 2. load history + append user turn -------------------------
        messages: list[AnyMessage] = self.store.load(sid)
        if not messages:
            # First turn · seed with the system prompt.
            messages = [SystemMessage(content=render_system_prompt(self._profile_blob()))]
        else:
            # Refresh the system prompt on every turn so profile updates
            # take effect immediately.  Cheap because of prompt caching.
            if isinstance(messages[0], SystemMessage):
                messages[0] = SystemMessage(content=render_system_prompt(self._profile_blob()))

        messages.append(HumanMessage(content=user_input))
        self.store.save(sid, messages)

        # ---- 3. main loop -----------------------------------------------
        llm = get_llm(temperature=0.2).bind_tools(self.tools)
        tools_by_name = self.tools_by_name

        for step in range(MAX_STEPS):
            # 3a. compaction · check token budget BEFORE every LLM call
            if needs_compaction(messages):
                tk_before = estimate_tokens(messages)
                emit("harness", "compaction",
                     {"action": "start", "tokens": tk_before, "step": step},
                     session_id=sid)
                # compact() is sync · run in default executor so we don't
                # block the event loop on the summariser LLM call
                messages = await asyncio.to_thread(compact, messages)
                self.store.save(sid, messages)
                tk_after = estimate_tokens(messages)
                emit("harness", "compaction",
                     {"action": "done", "tokens": tk_after, "saved": tk_before - tk_after},
                     session_id=sid)

            # 3b. stream the LLM response · token events go to the bus live
            response = await self._stream_llm(llm, messages, sid, step)
            messages.append(response)
            self.store.save(sid, messages)

            tool_calls = getattr(response, "tool_calls", None) or []

            # 3c. natural finish · no tool calls
            if not tool_calls:
                final_text = self._content_text(response.content)
                redacted = self._output_guardrail(final_text, sid)
                yield {"node": "harness", "kind": "answer", "payload": {"text": redacted}}
                return

            # 3d. explicit finish via final_answer tool
            for tc in tool_calls:
                if tc["name"] == "final_answer":
                    answer = (tc.get("args") or {}).get("answer", "")
                    # Append a synthetic ToolMessage so the message list
                    # stays valid (every tool_call must be answered).
                    messages.append(ToolMessage(
                        content="(final_answer accepted)",
                        name="final_answer",
                        tool_call_id=tc["id"],
                    ))
                    self.store.save(sid, messages)
                    redacted = self._output_guardrail(answer or self._content_text(response.content), sid)
                    yield {"node": "harness", "kind": "answer", "payload": {"text": redacted}}
                    return

            # 3e. run real tool calls · sequentially for simplicity here.
            # (Parallel tool execution is a 5-line change · see docs/tool-design.html)
            emit("harness", "tool_call",
                 {"calls": [{"name": c["name"], "args": c.get("args", {})} for c in tool_calls],
                  "step": step},
                 session_id=sid)

            for tc in tool_calls:
                name = tc["name"]
                tool = tools_by_name.get(name)
                if tool is None:
                    content = f"[error] unknown tool {name!r}"
                else:
                    content = await asyncio.to_thread(safe_run_tool, tool, tc.get("args", {}))
                emit("harness", "tool_result",
                     {"name": name, "chars": len(content), "preview": content[:240]},
                     session_id=sid)
                messages.append(ToolMessage(content=content, name=name, tool_call_id=tc["id"]))
                self.store.save(sid, messages)

        # ---- 4. hit the cap · ask for a forced wrap-up ------------------
        emit("harness", "error",
             {"error": f"hit MAX_STEPS={MAX_STEPS} · forcing finish"},
             session_id=sid)
        forced_msg = HumanMessage(
            content="You've reached the max step limit. Stop using tools and "
                    "give the user your best partial answer in 2-3 sentences."
        )
        messages.append(forced_msg)
        try:
            final = await asyncio.to_thread(get_llm(temperature=0.2).invoke, messages)
            text = self._content_text(final.content)
        except Exception as e:
            text = f"(internal error after max-step cap: {e!r})"
        redacted = self._output_guardrail(text, sid)
        yield {"node": "harness", "kind": "answer", "payload": {"text": redacted}}

    # ─────────────────────────────────────────── streaming helper

    async def _stream_llm(
        self,
        llm,
        messages: list[AnyMessage],
        session_id: str,
        step: int,
    ) -> AIMessage:
        """Stream the LLM response · publish each token chunk · return the
        accumulated AIMessage at the end.

        We use astream() (LangChain's standard streaming interface) instead
        of astream_events() because we control the loop · don't need the
        event-tree introspection that astream_events provides.
        """
        emit("harness", "llm_start", {"step": step}, session_id=session_id)

        chunks: list[AIMessageChunk] = []
        try:
            async for chunk in llm.astream(messages):
                chunks.append(chunk)
                # publish each text token to the SSE bus · the gateway/UI
                # already knows how to render kind="token" events
                pieces = self._extract_text_pieces(chunk.content)
                if pieces:
                    emit("harness", "token",
                         {"text": "".join(pieces)},
                         session_id=session_id)
        except Exception as e:
            emit("harness", "error", {"step": step, "error": repr(e)},
                 session_id=session_id)
            raise

        # Reduce all the chunks into one AIMessage · LangChain's chunks
        # support `+` for accumulation.
        if not chunks:
            return AIMessage(content="")
        acc = chunks[0]
        for c in chunks[1:]:
            acc = acc + c
        return AIMessage(
            content=acc.content,
            tool_calls=acc.tool_calls,
            additional_kwargs=acc.additional_kwargs,
            response_metadata=acc.response_metadata,
            id=acc.id,
        )

    # ─────────────────────────────────────────── content coercion

    @staticmethod
    def _content_text(content) -> str:
        """AIMessage.content can be str or list-of-blocks · normalise."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif isinstance(b, str):
                    parts.append(b)
            return "\n".join(p for p in parts if p)
        return str(content) if content is not None else ""

    @staticmethod
    def _extract_text_pieces(content) -> list[str]:
        if isinstance(content, str):
            return [content] if content else []
        if isinstance(content, list):
            out = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    t = b.get("text") or ""
                    if t:
                        out.append(t)
            return out
        return []


# ─────────────────────────────────────────────── module-singleton accessors


_default: Harness | None = None


def _get_harness() -> Harness:
    global _default
    if _default is None:
        _default = Harness()
    return _default


async def run_harness(user_input: str, session_id: str) -> AsyncIterator[dict]:
    """Convenience: yields events from the singleton Harness."""
    h = _get_harness()
    async for ev in h.run(user_input, session_id):
        yield ev
