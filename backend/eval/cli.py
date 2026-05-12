"""
Eval CLI · `python -m eval.cli [options]`

Examples
--------
# Run every golden case against BOTH engines, write report:
$ uv run python -m eval.cli

# Just math + memory, only harness, skip judge (faster smoke test):
$ uv run python -m eval.cli --categories math,memory --engines harness --no-judge

# Limit to first 5 cases per category (cheap smoke):
$ uv run python -m eval.cli --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .cases import load_all
from .judge import judge as judge_case
from .report import REPORT_PATH, render, write_jsonl_results
from .runner import run_case, to_dict

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "eval"


async def _process(args) -> None:
    cases = []
    for cat in args.categories:
        cases.extend(load_all(cat or None))
    if not args.categories:
        cases = load_all()

    if args.limit > 0:
        cases = cases[: args.limit]

    print(f"running {len(cases)} cases × {len(args.engines)} engines = {len(cases)*len(args.engines)} runs")

    merged: list[dict] = []
    for engine in args.engines:
        print(f"\n══ engine = {engine} ══")
        for c in cases:
            t0 = time.perf_counter()
            res = await run_case(c.id, c.user, engine)
            row = {**to_dict(res), "category": c.category, "user": c.user}
            if args.no_judge:
                # Programmatic checks are cheap · run them even without judge.
                ans_lc = (res.answer or "").lower()
                substring_pass = all(s.lower() in ans_lc for s in c.expected_substrings)
                forbidden_pass = not any(s.lower() in ans_lc for s in c.must_not_contain)
                expected_tools_pass = (
                    not c.expected_tools
                    or any(t in res.tools_used for t in c.expected_tools)
                )
                row.update({
                    "correctness": -1, "groundedness": -1, "efficiency": -1,
                    "score_pct": 0.0,
                    "substring_pass": substring_pass,
                    "forbidden_pass": forbidden_pass,
                    "expected_tools_pass": expected_tools_pass,
                    "judge_notes": "(judge skipped)",
                    "overall_pass": substring_pass and forbidden_pass and expected_tools_pass,
                })
            else:
                v = await judge_case(c, res)
                row.update({k: getattr(v, k) for k in (
                    "correctness", "groundedness", "efficiency", "score_pct",
                    "substring_pass", "forbidden_pass", "expected_tools_pass",
                    "judge_notes", "overall_pass",
                )})
            merged.append(row)
            tag = "✓" if row.get("overall_pass") else "✗"
            print(
                f"  [{tag}] {c.id:<14} {row['duration_s']:>6.2f}s  "
                f"score={row.get('score_pct',0):>5}  "
                f"tools={','.join(row['tools_used']) or '-':<24}  "
                f"err={row['error'] or ''}"
            )

    # Persist raw + render report
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RESULTS_DIR / f"{int(time.time())}.jsonl"
    write_jsonl_results(merged, raw_path)
    rep = render(merged, REPORT_PATH)

    # Summary line
    by_engine = {e: [r for r in merged if r["engine"] == e] for e in args.engines}
    print("\n══ summary ══")
    for e, rs in by_engine.items():
        if not rs:
            continue
        passed = sum(r.get("overall_pass") for r in rs)
        avg_lat = sum(r["duration_s"] for r in rs) / len(rs)
        avg_cost = sum(r["cost_usd"] for r in rs) / len(rs)
        print(f"  {e:<8} pass={passed}/{len(rs)}  avg_lat={avg_lat:.2f}s  avg_cost=${avg_cost:.4f}")
    print(f"\nraw   : {raw_path}")
    print(f"report: {rep}")


def main() -> None:
    p = argparse.ArgumentParser(prog="eval.cli")
    p.add_argument("--categories", default="", help="comma-separated subset (math,code,search,memory). Empty = all.")
    p.add_argument("--engines", default="graph,harness", help="comma-separated subset")
    p.add_argument("--limit", type=int, default=0, help="cap N cases per category (0 = all)")
    p.add_argument("--no-judge", action="store_true", help="skip LLM judge (programmatic checks only)")
    args = p.parse_args()
    args.categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    args.engines = [c.strip() for c in args.engines.split(",") if c.strip()]
    if not args.engines:
        print("--engines must include at least one of graph,harness", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_process(args))


if __name__ == "__main__":
    main()
