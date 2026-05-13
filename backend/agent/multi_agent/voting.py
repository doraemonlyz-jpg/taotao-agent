"""Multi-agent **voting** · N independent answers, majority wins (self-consistency).

Mental model:
  - Spawn N parallel agents with the SAME prompt + temperature > 0.
  - Each writes its answer + a short rationale.
  - Final is the most-frequent normalised answer; ties broken by the
    rationale-quality LLM judge.

Why use it (real production case):
  - Tasks with a small finite answer space (yes/no, A/B/C/D, numeric)
    where N=5 votes give a measurable lift over single-shot.  This is the
    "self-consistency" trick from Wang et al. 2022 (https://arxiv.org/abs/2203.11171).
  - Cheap to run in parallel via asyncio.gather.

When NOT to use:
  - Open-ended generation (prose, code) where there's no obvious "same
    answer" signal · debate or reflection beat voting there.

Reference: Wang et al., "Self-Consistency Improves Chain of Thought
Reasoning in Language Models", ICLR 2023.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter

from langchain_core.messages import HumanMessage, SystemMessage

from ..nodes.llm import get_fast_llm, get_llm
from ..observability import emit
from .types import AgentSpec, Result


_ANSWER_RE = re.compile(r"answer\s*[:：]\s*(.+?)$", re.IGNORECASE | re.MULTILINE)


def _normalise(answer: str) -> str:
    """Squash to lowercase, strip punctuation/whitespace · for vote-counting."""
    s = re.sub(r"[\s\W_]+", " ", answer.strip().lower()).strip()
    return s[:200]


def _extract_answer(text: str) -> str:
    """Pull the line after 'ANSWER:' or fall back to last non-empty line."""
    m = _ANSWER_RE.search(text)
    if m:
        return m.group(1).strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else text.strip()


def _one_voter(name: str, task: str, session_id: str, temperature: float) -> dict:
    sys = (f"You are voter '{name}' in a self-consistency panel.\n"
           "Solve the task INDEPENDENTLY · don't worry about other voters.\n"
           "Briefly show reasoning (≤80 words), then end with the line:\n"
           "ANSWER: <your answer>")
    msgs = [SystemMessage(content=sys), HumanMessage(content=task)]
    out = get_llm(temperature=temperature).invoke(msgs)
    text = out.content if isinstance(out.content, str) else str(out.content)
    ans = _extract_answer(text)
    emit("multi_agent", "vote_cast",
         {"voter": name, "answer": ans[:120]}, session_id=session_id)
    return {"voter": name, "raw": text, "answer": ans}


async def _gather(task: str, n: int, session_id: str, temperature: float) -> list[dict]:
    return await asyncio.gather(*[
        asyncio.to_thread(_one_voter, f"v{i+1}", task, session_id, temperature)
        for i in range(n)
    ])


def run_vote(
    task: str,
    *,
    n: int = 5,
    temperature: float = 0.7,
    session_id: str = "",
) -> Result:
    """Run N=5 voters in parallel · return majority answer.

    Ties broken by a fast-model judge that picks the better-justified
    answer · this is rare but happens on close 2-2-1 splits.
    """
    if n < 2:
        raise ValueError("voting needs n ≥ 2")
    if n > 11:
        raise ValueError("n > 11 is wasteful · self-consistency saturates around 5-7")

    emit("multi_agent", "vote_start",
         {"n": n, "temperature": temperature}, session_id=session_id)
    votes = asyncio.run(_gather(task, n, session_id, temperature))

    counter: Counter[str] = Counter(_normalise(v["answer"]) for v in votes)
    top, top_count = counter.most_common(1)[0]
    tied = [a for a, c in counter.items() if c == top_count]

    if len(tied) > 1:
        # tie-break · ask judge to pick the best-justified raw answer
        emit("multi_agent", "vote_tie",
             {"tied": tied, "count": top_count}, session_id=session_id)
        contenders = [v for v in votes if _normalise(v["answer"]) in tied]
        body = "\n\n".join(f"--- {v['voter']} ---\n{v['raw']}" for v in contenders)
        prompt = (
            f"Task: {task}\n\n"
            "Several voters tied on this answer.  Pick the BEST-justified "
            "one and reply with EXACTLY one line:\n"
            "WINNER: <answer text>\n\n"
            f"{body}"
        )
        try:
            j = get_fast_llm(temperature=0).invoke(prompt)
            jt = j.content if isinstance(j.content, str) else str(j.content)
            winner_line = next((ln for ln in jt.splitlines()
                                if ln.upper().startswith("WINNER:")), tied[0])
            final = winner_line.split(":", 1)[1].strip() if ":" in winner_line else tied[0]
        except Exception:
            final = tied[0]
    else:
        # find the original-cased answer for display
        first = next(v["answer"] for v in votes if _normalise(v["answer"]) == top)
        final = first

    emit("multi_agent", "vote_done",
         {"final": final[:200], "majority": top_count, "n": n},
         session_id=session_id)

    return Result(
        final=final,
        trace=[{"agent": v["voter"], "round": 1, "text": v["raw"]} for v in votes],
        meta={"n": n, "majority": top_count,
              "distribution": dict(counter), "tied": len(tied) > 1},
    )
