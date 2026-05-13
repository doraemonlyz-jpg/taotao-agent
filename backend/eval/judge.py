"""
LLM-as-judge for fuzzy correctness scoring.

Each case gets graded on three independent dimensions (0-2 each):
  - correctness:  Does the answer satisfy the rubric?
  - groundedness: Did it cite / use the right tools?
  - efficiency:   No useless tool calls or rambling?

Total score: 0-6 → normalised to 0-1.

We also do a deterministic substring check (`expected_substrings`) and
emit a hard-fail flag for `must_not_contain` hits — judge can be wrong,
substring can't.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

# Importing agent.config side-effect-loads the .env so init_chat_model
# below finds ANTHROPIC_API_KEY / OPENAI_API_KEY / etc.
from agent import config as _agent_config  # noqa: F401

from .cases import Case
from .runner import RunResult


@dataclass
class Verdict:
    case_id: str
    engine: str
    correctness: int        # 0 / 1 / 2
    groundedness: int
    efficiency: int
    score_pct: float        # (sum / 6) * 100
    substring_pass: bool    # all expected_substrings present?
    forbidden_pass: bool    # no must_not_contain hits?
    expected_tools_pass: bool
    judge_notes: str
    overall_pass: bool      # gate: substring AND forbidden AND score≥50


JUDGE_SYSTEM = (
    "You are a strict but fair grader for an AI agent's response. "
    "You return ONLY JSON in the shape "
    "`{\"correctness\":0|1|2,\"groundedness\":0|1|2,\"efficiency\":0|1|2,\"notes\":\"…\"}`.\n\n"
    "Rubric:\n"
    "- correctness:  2 = answer fully satisfies the rubric, 1 = partial, 0 = wrong\n"
    "- groundedness: 2 = used appropriate tools where needed, 1 = mostly, 0 = made things up\n"
    "- efficiency:   2 = no wasted tool calls or rambling, 1 = minor waste, 0 = lots of noise\n\n"
    "Be terse in `notes`. NEVER add markdown, code fences, or commentary outside the JSON."
)


def _extract_json(s: str) -> dict:
    """Salvage JSON from a possibly markdown-wrapped LLM reply."""
    s = s.strip()
    # strip ```json … ``` fences
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        # last-ditch: regex out the first JSON object
        m = re.search(r"\{.*\}", s, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise


async def _ask_judge(case: Case, result: RunResult) -> dict:
    """Call the judge LLM. Defaults to anthropic claude-3-5-sonnet because
    you already have an Anthropic key wired."""
    # Default to Haiku 4.5: fast (~2s), cheap (~$1/MTok), and accurate
    # enough for binary/three-level grading. Override via EVAL_JUDGE_MODEL
    # if you want to spend on Sonnet.
    judge_model = os.environ.get("EVAL_JUDGE_MODEL", "anthropic:claude-haiku-4-5")
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(judge_model, temperature=0)
    user_prompt = (
        f"USER QUERY:\n{case.user}\n\n"
        f"RUBRIC: {case.rubric or '(see expected_substrings)'}\n\n"
        f"EXPECTED TOOLS (any subset is fine): {case.expected_tools}\n"
        f"TOOLS ACTUALLY USED:                {result.tools_used}\n\n"
        f"AGENT ANSWER:\n{result.answer}\n"
    )
    raw = await llm.ainvoke(
        [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
    )
    text = raw.content if isinstance(raw.content, str) else str(raw.content)
    return _extract_json(text)


async def judge(case: Case, result: RunResult) -> Verdict:
    """Run programmatic checks + LLM judge on one case·result pair."""
    ans_lc = (result.answer or "").lower()

    substring_pass = all(s.lower() in ans_lc for s in case.expected_substrings)
    forbidden_pass = not any(s.lower() in ans_lc for s in case.must_not_contain)
    expected_tools_pass = (
        not case.expected_tools
        or any(t in result.tools_used for t in case.expected_tools)
    )

    try:
        graded = await _ask_judge(case, result)
        c = int(graded.get("correctness", 0))
        g = int(graded.get("groundedness", 0))
        e = int(graded.get("efficiency", 0))
        notes = str(graded.get("notes", "")).strip()[:400]
    except Exception as exc:
        c = g = e = 0
        notes = f"(judge failure: {exc!r})"

    # Floor / ceiling so a buggy judge can't blow scores past the rubric.
    c, g, e = max(0, min(c, 2)), max(0, min(g, 2)), max(0, min(e, 2))
    score_pct = round(((c + g + e) / 6.0) * 100, 1)

    overall_pass = substring_pass and forbidden_pass and score_pct >= 50.0

    return Verdict(
        case_id=case.id,
        engine=result.engine,
        correctness=c,
        groundedness=g,
        efficiency=e,
        score_pct=score_pct,
        substring_pass=substring_pass,
        forbidden_pass=forbidden_pass,
        expected_tools_pass=expected_tools_pass,
        judge_notes=notes,
        overall_pass=overall_pass,
    )
