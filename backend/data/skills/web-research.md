---
name: web-research
description: Multi-source web research with citations
when_to_use: the user asks about recent events, news, releases, or current facts
---

# Recipe — web-research

For any question whose answer can change over time:

1. **Decompose** the question into 1-3 atomic search queries.
2. **Search each** with `web_search`. Don't trust a single source.
3. **Cross-check**: if two sources agree, treat as fact; if they disagree,
   say so explicitly.
4. **Cite inline**: after every factual claim, add a parenthetical with the
   source domain — e.g. "(per anthropic.com)".
5. **Date-anchor**: if the answer is time-sensitive, add the date you
   pulled the info ("as of 2026-05").
6. **Bullet output**: use 3-5 bullets, each ending with its citation.

If `web_search` returns nothing useful after 2 attempts, say so honestly
and ask the user for additional context.
