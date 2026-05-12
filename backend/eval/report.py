"""
Render the evaluation results as a static HTML report.

Single self-contained file → easy to share / pin to a PR / serve from
docs/. Includes:
  - top summary (pass-rate, avg cost, avg latency) per engine
  - per-category breakdown
  - per-case A/B table (graph vs harness side-by-side)
  - sortable / scrollable

No JS framework — vanilla CSS + a tiny inline `<script>` for sort.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import statistics as stats
from pathlib import Path

REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "eval-report.html"


def _fmt_pct(x: float) -> str:
    return f"{x:.1f}%"


def _fmt_usd(x: float) -> str:
    if x == 0:
        return "$0"
    if x < 0.001:
        return "<$0.001"
    return f"${x:.4f}"


def _agg(rows: list[dict], engine: str, cat: str | None = None) -> dict:
    rs = [r for r in rows if r["engine"] == engine and (cat is None or r["category"] == cat)]
    if not rs:
        return {"n": 0}
    return {
        "n": len(rs),
        "pass_rate": _fmt_pct(100 * sum(r["overall_pass"] for r in rs) / len(rs)),
        "score": _fmt_pct(stats.mean(r["score_pct"] for r in rs)),
        "lat_ms": f"{stats.mean(r['duration_s'] for r in rs) * 1000:.0f}",
        "tokens_in": int(stats.mean(r["tokens_in"] for r in rs)),
        "tokens_out": int(stats.mean(r["tokens_out"] for r in rs)),
        "cost": _fmt_usd(stats.mean(r["cost_usd"] for r in rs)),
        "tools": int(stats.mean(len(r["tools_used"]) for r in rs)),
    }


def render(merged_rows: list[dict], out_path: Path = REPORT_PATH) -> Path:
    """`merged_rows`: each row has both run + verdict fields flattened."""
    cats = sorted({r["category"] for r in merged_rows})
    engines = ["graph", "harness"]
    summary = {e: _agg(merged_rows, e) for e in engines}
    by_cat = {(e, c): _agg(merged_rows, e, c) for e in engines for c in cats}

    # Pivot per-case so graph + harness are side by side
    by_case: dict[str, dict[str, dict]] = {}
    for r in merged_rows:
        by_case.setdefault(r["case_id"], {})[r["engine"]] = r

    def pill(text: str, color: str) -> str:
        return f'<span class="pill" style="background:{color}">{html.escape(text)}</span>'

    def case_row(cid: str) -> str:
        g = by_case[cid].get("graph") or {}
        h = by_case[cid].get("harness") or {}
        ref = g or h
        pass_g = "✓" if g.get("overall_pass") else "✗"
        pass_h = "✓" if h.get("overall_pass") else "✗"
        return f"""
        <tr>
          <td class="cid">{html.escape(cid)}</td>
          <td>{html.escape(ref.get("category", ""))}</td>
          <td class="user" title="{html.escape(ref.get('user',''))}">{html.escape((ref.get('user','') or '')[:80])}{'…' if len(ref.get('user',''))>80 else ''}</td>
          <td class="num pass-{'y' if g.get('overall_pass') else 'n'}">{pass_g}</td>
          <td class="num">{g.get('score_pct','-')}</td>
          <td class="num">{int((g.get('duration_s',0) or 0)*1000)}ms</td>
          <td class="num">{_fmt_usd(g.get('cost_usd',0) or 0)}</td>
          <td class="num pass-{'y' if h.get('overall_pass') else 'n'}">{pass_h}</td>
          <td class="num">{h.get('score_pct','-')}</td>
          <td class="num">{int((h.get('duration_s',0) or 0)*1000)}ms</td>
          <td class="num">{_fmt_usd(h.get('cost_usd',0) or 0)}</td>
          <td class="notes" title="graph: {html.escape(g.get('judge_notes',''))} ··· harness: {html.escape(h.get('judge_notes',''))}">{html.escape((h.get('judge_notes') or g.get('judge_notes') or '')[:60])}</td>
        </tr>"""

    summary_cards = "".join(
        f"""
        <div class="card">
          <div class="card-h">{e.upper()}</div>
          <div class="kv"><span>cases</span><b>{summary[e].get('n','-')}</b></div>
          <div class="kv"><span>pass-rate</span><b>{summary[e].get('pass_rate','-')}</b></div>
          <div class="kv"><span>avg score</span><b>{summary[e].get('score','-')}</b></div>
          <div class="kv"><span>avg latency</span><b>{summary[e].get('lat_ms','-')}ms</b></div>
          <div class="kv"><span>avg ↑/↓ tokens</span><b>{summary[e].get('tokens_in','-')} / {summary[e].get('tokens_out','-')}</b></div>
          <div class="kv"><span>avg cost</span><b>{summary[e].get('cost','-')}</b></div>
          <div class="kv"><span>avg tools/turn</span><b>{summary[e].get('tools','-')}</b></div>
        </div>"""
        for e in engines
    )

    cat_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(c)}</td>
          {"".join(f'<td class="num">{by_cat[(e,c)].get("pass_rate","-")}</td><td class="num">{by_cat[(e,c)].get("score","-")}</td><td class="num">{by_cat[(e,c)].get("lat_ms","-")}ms</td><td class="num">{by_cat[(e,c)].get("cost","-")}</td>' for e in engines)}
        </tr>"""
        for c in cats
    )

    case_rows = "".join(case_row(cid) for cid in sorted(by_case))

    when = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    html_doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Eval report · taotao-agent · {when}</title>
<style>
:root {{
  --bg:#f6f3e8;--paper:#fbf3df;--ink:#2c3a30;--ink-soft:#57665b;--ink-mute:#8a8c79;
  --rule:#c9b88e;--leaf:#8aa978;--forest:#5b7c52;--forest-deep:#3d5638;
  --sunset:#d8884a;--sunset-deep:#b56830;--rose:#c97a7a;
  --mono:'JetBrains Mono','SF Mono',Menlo,monospace;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,-apple-system,sans-serif;padding:2.2rem 2rem 4rem;max-width:1280px;margin:0 auto}}
h1{{font-size:1.8rem;margin:0 0 .25rem;color:var(--forest-deep)}}
.muted{{color:var(--ink-mute);font-family:var(--mono);font-size:.75rem;letter-spacing:.06em}}
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin:1.4rem 0 2rem}}
.card{{background:var(--paper);border:1px solid var(--rule);border-radius:14px;padding:1rem 1.2rem;box-shadow:0 2px 6px -2px rgba(61,86,56,.1)}}
.card-h{{font-family:var(--mono);font-size:.66rem;letter-spacing:.16em;color:var(--ink-mute);font-weight:700;margin-bottom:.6rem}}
.kv{{display:flex;justify-content:space-between;padding:.18rem 0;border-bottom:1px dotted var(--rule)}}
.kv:last-child{{border-bottom:0}}
.kv span{{color:var(--ink-soft);font-size:.78rem}}
.kv b{{font-family:var(--mono);font-size:.85rem;color:var(--ink)}}
table{{width:100%;border-collapse:collapse;margin:.8rem 0 1.6rem;background:var(--paper);border:1px solid var(--rule);border-radius:10px;overflow:hidden;font-size:.78rem}}
th{{background:rgba(125,169,130,.15);color:var(--forest-deep);padding:.5rem .55rem;text-align:left;font-family:var(--mono);font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;cursor:pointer}}
td{{padding:.42rem .55rem;border-top:1px solid rgba(201,184,142,.4);vertical-align:top}}
.num{{text-align:right;font-family:var(--mono);font-size:.78rem;white-space:nowrap}}
.cid{{font-family:var(--mono);font-size:.74rem;color:var(--ink-soft)}}
.user{{max-width:18rem;color:var(--ink)}}
.notes{{color:var(--ink-mute);font-size:.72rem;max-width:14rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.pass-y{{color:var(--forest-deep);font-weight:700}}
.pass-n{{color:var(--rose);font-weight:700}}
h2{{font-size:1.05rem;margin:1.6rem 0 .35rem;color:var(--forest-deep)}}
.split{{display:grid;grid-template-columns:1fr 1fr;gap:.8rem 1.2rem;margin-bottom:1.6rem}}
.split h3{{font-size:.78rem;font-family:var(--mono);letter-spacing:.1em;color:var(--ink-mute);text-transform:uppercase;margin:0 0 .4rem}}
.legend{{font-size:.7rem;color:var(--ink-mute);font-family:var(--mono)}}
.legend b{{color:var(--ink)}}
</style></head><body>

<h1>Eval Report · taotao-agent</h1>
<div class="muted">{when} · {len(merged_rows)//2} cases × 2 engines · judge={html.escape("anthropic:claude-3-5-sonnet-latest")}</div>

<div class="cards">{summary_cards}</div>

<h2>By category</h2>
<table>
<thead><tr><th>category</th>
{"".join(f'<th>{e} pass</th><th>{e} score</th><th>{e} lat</th><th>{e} cost</th>' for e in engines)}
</tr></thead>
<tbody>{cat_rows}</tbody>
</table>

<h2>Per-case A/B</h2>
<div class="legend">columns: <b>graph</b> (left half) vs <b>harness</b> (right half) · click ✓/✗ to scan failures</div>
<table>
<thead><tr>
  <th>case_id</th><th>cat</th><th>user</th>
  <th>G ✓</th><th>G score</th><th>G lat</th><th>G cost</th>
  <th>H ✓</th><th>H score</th><th>H lat</th><th>H cost</th>
  <th>judge notes</th>
</tr></thead>
<tbody>{case_rows}</tbody>
</table>

</body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def write_jsonl_results(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
