"""Multi-agent **debate** · N personas argue until they converge or hit max-rounds.

Mental model:
  - Round 1: each agent writes their independent answer + reasoning.
  - Round 2..K: each agent sees ALL prior answers and either reaffirms,
    refines, or concedes.  Convergence = all agents agree on the same
    bottom-line answer (we use a tiny LLM-as-judge to detect agreement,
    falling back to substring match on the last paragraph).
  - Final: a synth agent reads the transcript and writes the consensus.

Why use it (rare):
  - Hard reasoning where minority view often holds the truth.
  - Adversarial review (security / legal / policy) where you want a
    skeptic to push back on a default proposer.

When NOT to use it (default):
  - Anything where a single agent already gets it right · debate is 3-6×
    cost for marginal gains.

Reference: "Improving Factuality and Reasoning in Language Models through
Multiagent Debate" (Du et al., 2023, https://arxiv.org/abs/2305.14325)
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ..nodes.llm import get_fast_llm, get_llm
from ..observability import emit
from .types import AgentSpec, Result


def _agent_say(agent: AgentSpec, transcript: str, task: str, session_id: str) -> str:
    sys = (agent.system + "\n\n"
           "You are debating with other agents.  Be concise (≤120 words).\n"
           "If you change your mind based on others' arguments, say so.\n"
           "End with a single line: 'ANSWER: <your current answer>'.")
    msgs = [SystemMessage(content=sys),
            HumanMessage(content=f"Task: {task}\n\nTranscript so far:\n{transcript}")]
    llm = get_llm(temperature=0.4)
    out = llm.invoke(msgs)
    text = out.content if isinstance(out.content, str) else str(out.content)
    emit("multi_agent", "debate_say",
         {"agent": agent.name, "preview": text[:200]}, session_id=session_id)
    return text


def _converged(agents: list[AgentSpec], last_answers: dict[str, str]) -> bool:
    """Tiny LLM judge · returns True iff all agents agree on the bottom line."""
    if len(set(a.strip().lower() for a in last_answers.values())) == 1:
        return True
    body = "\n".join(f"- {n}: {a}" for n, a in last_answers.items())
    prompt = (
        "You are a strict judge.  Below are answers from multiple agents.\n"
        "Reply only YES or NO.  YES = they all agree on the same answer.\n\n"
        f"{body}"
    )
    try:
        out = get_fast_llm(temperature=0).invoke(prompt)
        return "yes" in (out.content if isinstance(out.content, str) else str(out.content)).lower()[:8]
    except Exception:
        return False


def run_debate(
    task: str,
    agents: list[AgentSpec] | None = None,
    *,
    max_rounds: int = 3,
    session_id: str = "",
) -> Result:
    """Run a debate · returns final consensus + transcript.

    Default panel (if `agents` is None):
      - "optimist"  · proposes
      - "skeptic"   · challenges
      - "synthesiser" · merges

    Stops at consensus OR after `max_rounds`.
    """
    panel = agents or [
        AgentSpec("optimist",     "You are OPTIMIST · propose the best answer · be direct."),
        AgentSpec("skeptic",      "You are SKEPTIC · find holes in proposals · be precise."),
        AgentSpec("synthesiser",  "You are SYNTHESISER · merge views into a defensible answer."),
    ]
    if len(panel) < 2:
        raise ValueError("debate needs at least 2 agents")

    transcript_lines: list[str] = []
    trace: list[dict] = []
    last_answers: dict[str, str] = {}
    rounds_used = 0

    emit("multi_agent", "debate_start",
         {"agents": [a.name for a in panel], "max_rounds": max_rounds},
         session_id=session_id)

    for r in range(1, max_rounds + 1):
        rounds_used = r
        for agent in panel:
            text = _agent_say(agent, "\n".join(transcript_lines) or "(empty)", task, session_id)
            transcript_lines.append(f"[{agent.name} · r{r}] {text}")
            trace.append({"agent": agent.name, "round": r, "text": text})
            ans_line = next((ln[len("ANSWER:"):].strip()
                             for ln in text.splitlines()
                             if ln.strip().upper().startswith("ANSWER:")), text[-200:])
            last_answers[agent.name] = ans_line
        if r >= 2 and _converged(panel, last_answers):
            emit("multi_agent", "debate_converged",
                 {"round": r}, session_id=session_id)
            break

    # Final synthesis · use the panel's last round + a fresh prompt
    synth_prompt = (
        f"Original task: {task}\n\n"
        f"Debate transcript:\n" + "\n".join(transcript_lines) +
        "\n\nWrite the final consensus answer in 1 paragraph (no preamble)."
    )
    try:
        final_msg = get_llm(temperature=0.2).invoke(synth_prompt)
        final = final_msg.content if isinstance(final_msg.content, str) else str(final_msg.content)
    except Exception as e:
        final = f"(debate finished but synthesis failed: {e})\n\nLast answers:\n" + \
                "\n".join(f"- {n}: {a}" for n, a in last_answers.items())

    emit("multi_agent", "debate_done",
         {"rounds": rounds_used, "preview": final[:240]}, session_id=session_id)

    return Result(
        final=final,
        trace=trace,
        meta={"rounds": rounds_used, "agents": [a.name for a in panel],
              "converged": rounds_used < max_rounds},
    )
