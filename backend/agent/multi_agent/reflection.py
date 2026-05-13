"""Multi-agent **reflection** · Reflexion / Self-Refine actor + critic loop.

Mental model:
  - Round 1: ACTOR produces a draft answer.
  - CRITIC scores it (1-10) and writes specific revision suggestions.
  - If score ≥ pass_score, stop.  Else ACTOR rewrites using the critique.
  - Repeat up to `max_rounds` (default 3).

Why use it (real production case):
  - Code generation where compile-then-fix loop demonstrably lifts SWE-bench.
  - Long-form writing where draft → critique → revise beats single-shot.
  - Plans / RFCs where critic catches missing sections (security / SLO / cost).

When NOT to use:
  - Simple Q&A · the actor's first answer is usually fine.
  - Tasks where the critic can't verify (no ground truth + no rubric).
    The "critic LLM rates its own kid" failure mode is real.

Reference: Shinn et al., "Reflexion: Language Agents with Verbal
Reinforcement Learning", NeurIPS 2023 (https://arxiv.org/abs/2303.11366)
And: Madaan et al., "Self-Refine: Iterative Refinement with Self-Feedback"
(https://arxiv.org/abs/2303.17651)
"""
from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from ..nodes.llm import get_fast_llm, get_llm
from ..observability import emit
from .types import AgentSpec, Result

_SCORE_RE = re.compile(r"score\s*[:：]\s*(\d{1,2})", re.IGNORECASE)


def _actor_round(actor: AgentSpec, task: str, prior: str | None,
                 critique: str | None, session_id: str, r: int) -> str:
    sys = (actor.system + "\n\n"
           "You are the ACTOR · produce or revise the answer.\n"
           "Be self-contained · the user only sees your final reply.")
    user_parts = [f"Task: {task}"]
    if prior:
        user_parts.append(f"\nYour previous draft:\n{prior}")
    if critique:
        user_parts.append(f"\nCritic feedback:\n{critique}")
        user_parts.append("\nRevise the draft addressing the critique. "
                          "Output the FULL new answer, not just a diff.")
    out = get_llm(temperature=0.4).invoke(
        [SystemMessage(content=sys),
         HumanMessage(content="\n".join(user_parts))]
    )
    text = out.content if isinstance(out.content, str) else str(out.content)
    emit("multi_agent", "reflection_actor",
         {"round": r, "preview": text[:200]}, session_id=session_id)
    return text


def _critic_round(critic: AgentSpec, task: str, draft: str,
                  session_id: str, r: int) -> tuple[int, str]:
    sys = (critic.system + "\n\n"
           "You are the CRITIC · review the actor's draft.\n"
           "Output EXACTLY in this format:\n"
           "SCORE: <integer 1-10>\n"
           "ISSUES:\n"
           "  - <issue 1>\n"
           "  - <issue 2>\n"
           "FIXES:\n"
           "  - <concrete fix 1>\n"
           "  - <concrete fix 2>\n\n"
           "Be strict but specific.  10 = ship it.  Below 7 needs revision.")
    out = get_fast_llm(temperature=0.2).invoke(
        [SystemMessage(content=sys),
         HumanMessage(content=f"Task: {task}\n\nDraft to review:\n{draft}")]
    )
    text = out.content if isinstance(out.content, str) else str(out.content)
    m = _SCORE_RE.search(text)
    score = int(m.group(1)) if m else 5  # be cautious if format slipped
    score = max(1, min(10, score))
    emit("multi_agent", "reflection_critic",
         {"round": r, "score": score, "preview": text[:200]},
         session_id=session_id)
    return score, text


def run_reflection(
    task: str,
    *,
    actor: AgentSpec | None = None,
    critic: AgentSpec | None = None,
    max_rounds: int = 3,
    pass_score: int = 8,
    session_id: str = "",
) -> Result:
    """Run actor↔critic loop · stop when score ≥ pass_score or max_rounds.

    Defaults · adequate for code / writing review:
      actor   = "expert practitioner who writes the answer"
      critic  = "rigorous reviewer who scores 1-10 and demands fixes"
    """
    actor = actor or AgentSpec(
        "actor",
        "You are an EXPERT PRACTITIONER · clear, concrete, no fluff.",
    )
    critic = critic or AgentSpec(
        "critic",
        "You are a RIGOROUS SENIOR REVIEWER · find weaknesses, demand "
        "specific fixes · grade strictly.",
    )

    emit("multi_agent", "reflection_start",
         {"max_rounds": max_rounds, "pass_score": pass_score},
         session_id=session_id)

    draft = ""
    critique: str | None = None
    score = 0
    trace: list[dict] = []

    for r in range(1, max_rounds + 1):
        draft = _actor_round(actor, task, draft or None, critique, session_id, r)
        trace.append({"agent": actor.name, "round": r, "text": draft})
        score, critique = _critic_round(critic, task, draft, session_id, r)
        trace.append({"agent": critic.name, "round": r, "text": critique,
                      "score": score})
        if score >= pass_score:
            emit("multi_agent", "reflection_pass",
                 {"round": r, "score": score}, session_id=session_id)
            break

    emit("multi_agent", "reflection_done",
         {"rounds": r, "final_score": score}, session_id=session_id)

    return Result(
        final=draft,
        trace=trace,
        meta={"rounds": r, "final_score": score, "passed": score >= pass_score,
              "pass_score": pass_score},
    )
