"""
Lightweight in-process eval framework for taotao-agent.

Run a fixed `golden/*.jsonl` set against either backend (graph or
harness) and produce:
  - per-case JSONL with answer, latency, tokens, tool calls, judge verdict
  - one summary `docs/eval-report.html` with charts + side-by-side A/B

Entry point: `python -m eval.cli --engine harness`
"""
