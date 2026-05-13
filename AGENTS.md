# AGENTS.md · 桃桃 agent · project-level instructions

Loaded automatically into every system prompt by `agent/project_context.py`.
Edits here take effect on the next /chat call · no restart needed.

## Project

This repo is `taotao-agent` · a teaching-grade industrial agent demo with two
interchangeable backends (LangGraph 13-node graph and Claude-Code-style
harness while-loop) sharing one tool registry, observability, memory layer,
and frontend.

The reader is studying for a senior agent-engineer role · prefer answers
that explain the *why* alongside the code, and call out tradeoffs.

## Style

- Be concise. Senior readers don't need beginner caveats.
- Code blocks: prefer fenced ``` over text descriptions.
- Quote sources / paths / line numbers when you cite the codebase.
- When you propose an edit, ALWAYS use `propose_edit` first (shows diff)
  before `write_file` · except when the file is brand-new and trivial.
- When a query needs > 1 independent web search, fan them out as parallel
  `dispatch_subagent(role="researcher", ...)` calls in the same turn.

## Don'ts

- Don't run `python_repl` to verify a 1-line calculation · use `calculator`.
- Don't call `web_search` if you already cited that source this session.
- Don't summarise the conversation unless asked · we have `/compact` for that.

## House rules

- We're on Python 3.10+ · `from __future__ import annotations` is fine.
- We pin `model = anthropic:claude-sonnet-4-5-20250929` and
  `fast_model = anthropic:claude-haiku-4-5`.  Don't suggest GPT-style code
  paths unless the user explicitly switches model.
- All tool execution goes through `safe_run_tool` (timeout / cache /
  truncation / permission / hooks).  When teaching, mention this pipeline
  explicitly.
