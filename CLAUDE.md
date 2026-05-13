# CLAUDE.md · taotao-agent · AI coding-agent onboarding

> **Audience**: AI coding agents (Claude Code, Cursor, Codex CLI, Continue,
> Cline, Aider, etc.) that are picking up this repo cold.
> **Goal**: After reading this single file, you should know what this project is,
> how it's organised, how to run it, what conventions to follow, what's been
> built, and what the next reasonable units of work look like.
>
> **Read me in full before making changes.** This file is intentionally
> ~700 lines · everything below is load-bearing.

---

## ⚠️ Two `AGENTS.md` files exist · don't confuse them

| File | Purpose | Loaded when |
|---|---|---|
| `AGENTS.md` (root) | **Runtime** project rules · injected into the agent's *system prompt* on every `/chat` call by `backend/agent/project_context.py` | Every chat request |
| `CLAUDE.md` (you are here) | **Developer** onboarding for AI coding agents working *on* the codebase | When an external IDE/CLI agent reads the repo |

If you edit `AGENTS.md` you change the running agent's behaviour.
If you edit `CLAUDE.md` you change what future AI coders learn about the repo.
**They serve different audiences. Don't merge them.**

---

## §1 · TL;DR (60 seconds)

`taotao-agent` is a **teaching-grade industrial-quality LLM agent** + a
**24-book senior-agent-engineer interview manual** (~35k lines of HTML docs)
sitting next to it. The repo is intentionally over-engineered so every
classic agent component (planner / supervisor / executor / critic /
short-term + long-term memory / RAG / tool gate / observability / MCP /
multi-agent / sandbox) exists as a real, traceable, modifiable node.

Two backends cohabit and share state:

- `POST /chat`     · **LangGraph 13-node graph** (`agent/graph.py`)
- `POST /chat/v2`  · **Claude-Code-style harness while-loop** (`agent/harness/`)

Both share: tools registry, memory layer, MCP, multi-agent patterns,
permissions, hooks, observability, project context. Frontend is React+Vite,
backend is FastAPI on Python 3.10+.

**Status (2026-05-13)**: 24 docs books complete · 4 multi-agent patterns
shipped · industrial-grade (Book 22) features all implemented · multi-tenant
auth + memory namespacing shipped (Book 24 SaaS starter). Tier-2 (internal
SaaS) ready · Tier-3 (public SaaS) needs the rest of Book 24's roadmap.

---

## §2 · Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend lang | Python 3.10+ | LangGraph / LangChain ecosystem |
| Backend framework | FastAPI | SSE, async, OpenAPI codegen |
| Agent framework | LangGraph (graph) + bespoke harness loop | Teach both paradigms |
| LLM | Anthropic Claude Sonnet 4.5 (default) · OpenAI / Google / Ollama all wired | Model-agnostic via `init_chat_model` |
| Vector DB | Chroma (local persistent) | Zero-config dev · upgradeable to Pinecone/Qdrant for prod |
| Checkpoint store | SQLite via `langgraph.checkpoint.sqlite` | Single-file simplicity · upgradeable to Postgres |
| Frontend | React 19 + Vite + TS | OpenAPI codegen via `openapi-typescript` |
| Observability | OpenTelemetry + Sentry + Prometheus + custom JSONL trace bus | Multi-sink |
| Auth (legacy) | `X-API-Key` header (env-gated) | Dev-friendly default |
| Auth (SaaS) | JWT (RS256) + tenant claim | `backend/agent/auth/identity.py` |
| Container | Docker + docker-compose | One-command stack |
| Package manager (Py) | `uv` (preferred) · `pip` works | `pyproject.toml` + `uv.lock` |
| Package manager (JS) | `npm` | `package-lock.json` |

**Pinned models** (don't change without reason):
- `AGENT_MODEL=anthropic:claude-sonnet-4-5-20250929`
- `AGENT_FAST_MODEL=anthropic:claude-haiku-4-5`

---

## §3 · Repo map (annotated · key files only)

```
taotao-agent/
├── AGENTS.md                 ⚠ Runtime · injected into system prompt
├── CLAUDE.md                 ⚠ You are here · for AI coders
├── README.md                 Human-facing project README + 24-book index
├── DOCKER.md                 Docker / compose how-to
├── docker-compose.yml        agent (FastAPI) + gateway (Go proxy)
│
├── backend/                  Python · the agent itself
│   ├── app.py                FastAPI surface · /chat /chat/v2 /memory /traces ...
│   ├── taotao_cli.py         Claude-Code-style CLI · --continue --resume --plan
│   ├── pyproject.toml        Deps · use `uv sync` to install
│   ├── .env.example          All env vars documented · copy to .env
│   ├── mcp_servers.example.json   MCP client config · enables external MCP servers
│   │
│   ├── agent/                The brains
│   │   ├── config.py         Settings dataclass (env-driven)
│   │   ├── state.py          AgentState TypedDict (graph state)
│   │   ├── graph.py          ★ LangGraph 13-node graph wiring
│   │   ├── project_context.py    Loads AGENTS.md → system prompt
│   │   ├── security.py       require_api_key, slowapi rate limiter, Sentry init
│   │   ├── permissions.py    allow/ask/deny gate before tool exec (Book 22 feat)
│   │   ├── hooks.py          pre/post tool hooks (Book 22 feat)
│   │   ├── slash_commands.py /compact /clear /plan /resume CLI commands
│   │   ├── models_catalog.py Ollama / Anthropic / OpenAI model discovery
│   │   │
│   │   ├── auth/             ★ Multi-tenant identity (Book 24 starter)
│   │   │   ├── __init__.py   re-exports Identity, current_identity
│   │   │   └── identity.py   JWT verify · API_KEY fallback · dev mode · Identity dataclass
│   │   │
│   │   ├── nodes/            LangGraph node implementations (one file each)
│   │   │   ├── perception.py     read user msg
│   │   │   ├── planner.py        ReAct plan
│   │   │   ├── supervisor.py     route to next node
│   │   │   ├── executor.py       call tools
│   │   │   ├── critic.py         self-check
│   │   │   ├── llm.py            base LLM call
│   │   │   ├── extractor.py      structured output
│   │   │   ├── summarizer.py     compaction
│   │   │   └── guardrails.py     input/output safety
│   │   │
│   │   ├── harness/          ★ Claude-Code-style while-loop (Book 13)
│   │   │   ├── loop.py       The main loop · 392 LOC · the heart
│   │   │   ├── tools.py      HARNESS_TOOLS registry (LangChain tools)
│   │   │   ├── prompt.py     System prompt template
│   │   │   ├── persistence.py    ~/.taotao/sessions/*.jsonl
│   │   │   ├── compaction.py     auto-compact at token threshold
│   │   │   └── subagent.py   dispatch_subagent tool implementation
│   │   │
│   │   ├── tools/            One file per tool · LangChain @tool decorated
│   │   │   ├── registry.py   GRAPH_TOOLS (graph backend list)
│   │   │   ├── safe_exec.py  ★ safe_run_tool wrapper · timeout/cache/permission/hook
│   │   │   ├── router.py     LLM-based tool selection
│   │   │   ├── calculator.py current_time.py file_ops.py memory_tool.py
│   │   │   ├── profile_tool.py propose_edit.py python_repl.py
│   │   │   ├── read_tool_result.py    Book 22 · re-read truncated tool output
│   │   │   ├── skills_tool.py         Book 22 · agent skills system
│   │   │   ├── multi_agent_tool.py    ★ exposes the 4 patterns to the harness LLM
│   │   │   └── web_search.py Tavily preferred · DuckDuckGo fallback
│   │   │
│   │   ├── memory/           Multi-tier memory
│   │   │   ├── short_term.py     window buffer + summary
│   │   │   ├── long_term.py  ★ Chroma · NOW per-tenant collection (Book 24)
│   │   │   ├── profile.py    durable user profile (key=value)
│   │   │   ├── reflections.py    nightly self-reflection notes
│   │   │   └── skills.py     learned procedural skills
│   │   │
│   │   ├── mcp/              Dual-role MCP (Book 16)
│   │   │   ├── server.py     We expose tools as MCP server
│   │   │   ├── client.py     We call other MCP servers as client
│   │   │   └── __main__.py   `python -m agent.mcp` launches server stdio
│   │   │
│   │   ├── multi_agent/      ★ 4 multi-agent patterns (Book 23)
│   │   │   ├── types.py      AgentSpec, Result dataclasses (shared types)
│   │   │   ├── debate.py     N agents argue until consensus + LLM-judge
│   │   │   ├── voting.py     N independent + majority + tiebreak
│   │   │   ├── handoff.py    OpenAI-Swarm-style explicit transfer
│   │   │   └── reflection.py actor-critic loop (Reflexion / Self-Refine)
│   │   │
│   │   ├── observability/    ★ Three sinks
│   │   │   ├── tracer.py     Custom JSONL bus (data/traces/*.jsonl)
│   │   │   ├── telemetry.py  OpenTelemetry + Prometheus init
│   │   │   └── usage.py      token / cost / per-session budget guardrail (P1)
│   │   │
│   │   └── subagents/        Book 22 · subagent-as-markdown-file
│   │       └── loader.py     reads .agent/subagents/*.md as agent definitions
│   │
│   ├── eval/                 Eval harness · `python -m eval.cli --limit N`
│   ├── prompts/              Versioned prompt templates
│   ├── data/                 ⚠ gitignored · chroma + sqlite + traces
│   └── Dockerfile            Production container for the backend
│
├── frontend/                 React app · port 5180
│   ├── src/
│   │   ├── App.tsx           Main shell · multi-session sidebar (P1 feat)
│   │   ├── api/schema.gen.ts ★ AUTO-GENERATED from FastAPI OpenAPI
│   │   └── components/       MessageList, Composer, etc.
│   ├── package.json          `npm run gen:api` regenerates schema.gen.ts
│   ├── vite.config.ts
│   ├── Dockerfile            ⚠ unused · prefer Dockerfile.prod
│   └── Dockerfile.prod       ★ multi-stage node→nginx (Book 24 starter)
│
├── deploy/
│   └── nginx/
│       └── taotao.conf.example   ★ production nginx (TLS+headers+SSE) (Book 24)
│
├── clients/                  Reference SDKs
│   ├── go-client/            Go gateway (in docker-compose) · simple proxy
│   └── ts-client/            TypeScript SDK (uses schema.gen.ts)
│
└── docs/                     ★ The 24-book interview manual (~35k lines HTML)
    ├── index.html            Landing page · 8-phase learning roadmap
    ├── _assets/
    │   └── mermaid-init.html Custom-themed Mermaid loader (dark Petit-Prince)
    ├── learn.html            Book 01 · agent fundamentals (110 terms / 60 Q)
    ├── implementation.html   Book 02 · field-guide to THIS codebase
    ├── langgraph-internals.html Book 03 · LangGraph 6 primitives + 5 frameworks
    ├── rag-production.html   Book 04 · production RAG
    ├── memory-context.html   Book 05 · memory & context window
    ├── tool-design.html      Book 06 · tool design + §11 security 30 tactics
    ├── eval-observability.html   Book 07 · eval + obs (LLM-judge, CI gate)
    ├── agent-debugging.html  Book 08 · debugging playbook
    ├── enterprise.html       Book 09 · enterprise delivery (RACI, 12-layer stack)
    ├── agent-design.html     Book 10 · system design (interview)
    ├── agent-coding.html     Book 11 · live coding (interview)
    ├── infra.html            Book 12 · AI infra (含 §1.5 LLM 30Q patch)
    ├── harness.html          Book 13 · harness engineering complete
    ├── harness-vs-graph.html Book 14 · graph vs harness side-by-side
    ├── hermes.html           Book 15 · Hermes / Nous models speedread
    ├── mcp.html              Book 16 · MCP USB-C protocol
    ├── p1-hardening.html     Book 17 · P1 prod hardening (含 §8 cost-latency)
    ├── behavioral.html       Book 18 · behavioral interview + project deep-dive
    ├── post-training.html    Book 19 · SFT/DPO/RLHF/RLAIF
    ├── multimodal-voice.html Book 20 · VLM / voice agents
    ├── benchmarks.html       Book 21 · agent benchmarks reading guide
    ├── industrial.html       Book 22 · industrial infra (Claude-Code reverse-eng)
    ├── multi-agent.html      Book 23 · multi-agent architecture
    ├── saas.html             Book 24 · demo → public SaaS · 11-phase roadmap
    └── eval-report.html      live eval report (auto-generated)
```

---

## §4 · The two backends · `/chat` vs `/chat/v2`

This is the most important architectural fact about the repo.

### `POST /chat` · LangGraph backend
- Entry: `app.py` `/chat` → `agent/graph.py::build_graph()`
- 13 nodes · perception → planner → supervisor → executor → critic → ...
- Explicit DAG · easier to reason about, harder to extend mid-loop
- Good for: structured workflows, demos, reasoning visualisation
- Trace shows up in `data/traces/` and OTLP

### `POST /chat/v2` · Harness backend
- Entry: `app.py` `/chat/v2` → `agent/harness/loop.py::HarnessLoop.run()`
- Single while-loop calling LLM with tools until done
- Mimics Claude Code's actual architecture (`Book 13` documents the
  reverse-engineering)
- Good for: open-ended tasks, code editing, agentic coding
- Same trace surface

### What they share
- `agent/tools/registry.py` (graph) and `agent/harness/tools.py` (harness)
  · both LangChain `@tool`-decorated · safe_exec wraps them identically
- `agent/memory/*` long-term Chroma + profile + reflections
- `agent/permissions.py` allow/ask/deny gate
- `agent/hooks.py` pre/post tool hooks
- `agent/observability/*` tracing + OTLP + Prometheus
- `agent/auth/identity.py` (new · Book 24)
- `agent/multi_agent/*` (new · Book 23)
- `agent/mcp/*` MCP client + server
- `agent/project_context.py` injects `AGENTS.md` into system prompt

### Pick which to extend
- New tool → add to **both** registries (graph + harness)
- New memory feature → put in `agent/memory/`, both backends pick up
- New conversation pattern → harness side (graph is intentionally fixed)
- New routing logic → graph side (`agent/nodes/supervisor.py`)

---

## §5 · Run commands

### First-time setup

```bash
cd backend && cp .env.example .env       # fill ONE provider key
cd backend && uv sync                     # install Python deps (or pip install -e .)
cd frontend && npm install               # frontend deps
```

### Daily dev loop (open 2 terminals)

```bash
# Terminal 1 · backend (port 8000)
cd backend && source .venv/bin/activate
uvicorn app:app --reload --port 8000

# Terminal 2 · frontend (port 5180)
cd frontend && npm run dev
# → open http://localhost:5180
```

### CLI (Claude-Code-style)

```bash
cd backend && source .venv/bin/activate
python taotao_cli.py "list files in this repo and summarise"
python taotao_cli.py --plan "redesign the auth flow"   # plan mode
python taotao_cli.py --continue                        # resume last session
python taotao_cli.py --resume <session_id>             # specific session
```

### Docker (whole stack)

```bash
docker compose up -d --build
# agent on :8000 · gateway on :8080 · curl http://localhost:8080/health
```

### Production frontend build

```bash
cd frontend && docker build -t taotao-frontend:prod \
  --build-arg VITE_API_BASE=https://api.example.com \
  -f Dockerfile.prod .
```

### MCP server mode (expose ourselves)

```bash
cd backend && python -m agent.mcp        # stdio MCP server
# Now Claude Desktop / Cursor / Cline can add us as an MCP server
```

### Eval

```bash
cd backend && python -m eval.cli --limit 10
# → updates docs/eval-report.html
```

### Regenerate frontend types from backend OpenAPI

```bash
cd frontend && npm run gen:api
# → src/api/schema.gen.ts updated · TS-checked against backend
```

---

## §6 · Conventions (read before editing)

### Style

- **Python**: 3.10+ · `from __future__ import annotations` is fine.
  Type hints required on public functions. dataclasses preferred over
  attrs. `pathlib.Path` over `os.path`.
- **TypeScript**: strict mode on. Use the auto-generated types from
  `src/api/schema.gen.ts` · don't hand-roll API types.
- **HTML docs**: Vanilla HTML + inline `<style>` (no build step).
  Match the Petit-Prince dark palette of existing books. Use Mermaid
  via `_assets/mermaid-init.html` for diagrams · never raw ASCII art.
- **Comments**: only when intent isn't obvious from the code. NEVER
  narrate ("// increment counter"). Document non-obvious tradeoffs,
  not what the code does.

### File organisation

| What | Where | Pattern |
|---|---|---|
| New tool | `backend/agent/tools/<name>.py` | LangChain `@tool` · register in `tools/registry.py` AND `harness/tools.py` |
| New graph node | `backend/agent/nodes/<name>.py` | Plain function `(state) -> state` · wire in `agent/graph.py` |
| New memory feature | `backend/agent/memory/<name>.py` | One class per file · stateless instances per request |
| New endpoint | `backend/app.py` | Add `Depends(current_identity)` for tenant-aware, or `Depends(require_api_key)` for legacy |
| New multi-agent pattern | `backend/agent/multi_agent/<name>.py` | Match the `Result` shape · expose via `multi_agent_run` tool |
| New book / chapter | `docs/<slug>.html` | Self-contained HTML · register in `docs/index.html` (3 places: nav, quick-start, all-books grid) AND `README.md` |

### Tool execution

**ALL tool calls MUST go through `safe_run_tool` in
`backend/agent/tools/safe_exec.py`.** It provides:
1. Timeout (per-tool configurable)
2. Result cache (deterministic tools only)
3. Permission gate (allow/ask/deny)
4. Pre/post hooks
5. Output truncation + offset metadata (so `read_tool_result` can resume)

Don't invoke tool functions directly. Don't bypass `safe_run_tool` even
for "simple" cases · the harness LLM relies on consistent metadata.

### Multi-tenant memory

After Book 24, `LongTermMemory()` defaults to `tenant_id="default"` for
backward compatibility. Multi-tenant callers MUST use:

```python
from agent.auth import current_identity, Identity
from agent.memory.long_term import LongTermMemory

@app.post("/your-route")
async def yours(ident: Identity = Depends(current_identity)):
    mem = LongTermMemory.for_tenant(ident.tenant_id)   # ← physical isolation
    ...
```

**Never** use a single `LongTermMemory()` shared across tenants in
multi-tenant code paths. The collection-per-tenant model is what
defends against the #1 SaaS data-leak bug.

### Auth

- **Legacy single-tenant**: `Depends(require_api_key)` (env: `API_KEY=...`)
- **Multi-tenant SaaS**: `Depends(current_identity)` (env: `JWT_PUBLIC_KEY=...`)
- Both can coexist · the new identity dependency falls back to the
  legacy API_KEY shape, then to dev mode.
- All `/admin/*` and mutating endpoints MUST have one of the above.

### Observability

Every notable event should hit the trace bus:

```python
from agent.observability.tracer import trace
trace("tool.executed", tool=name, ms=elapsed_ms, ok=True)
```

OTLP + Prometheus pickup is automatic. Don't `print()` for diagnostics ·
use `logging` (already configured) or `trace()`.

---

## §7 · The 24 books (interview manual)

All under `docs/*.html`. Read order is *suggested*, not enforced.

| # | Book | File | What it covers |
|---|---|---|---|
| 01 | 应用入门 | `learn.html` | Agent fundamentals · 13 ch · 110 terms · 60 Q |
| 02 | Implementation Field Guide | `implementation.html` | Walks through THIS codebase node-by-node · 17 ch |
| 03 | LangGraph 内部 + 5 框架 | `langgraph-internals.html` | 6 primitives + Pregel source + framework comparison |
| 04 | RAG 生产级 | `rag-production.html` | Chunking / embeddings / hybrid / re-rank |
| 05 | Memory & Context | `memory-context.html` | Multi-tier memory · context window mgmt |
| 06 | Tool Design | `tool-design.html` | + §11 security 30 attack/defence tactics |
| 07 | Eval & Obs | `eval-observability.html` | LLM-judge · CI gate · 4 golden signals |
| 08 | Debugging Playbook | `agent-debugging.html` | Production incident response |
| 09 | Enterprise Delivery | `enterprise.html` | RACI · spec template · 12-layer stack |
| 10 | System Design | `agent-design.html` | Interview-style system design |
| 11 | Live Coding | `agent-coding.html` | Interview-style live coding |
| 12 | AI Infra | `infra.html` | + §1.5 LLM 30 Q quick-ref patch |
| 13 | Harness Complete | `harness.html` | Reverse-engineered Claude Code loop |
| 14 | Harness vs Graph | `harness-vs-graph.html` | Side-by-side comparison |
| 15 | Hermes Speedread | `hermes.html` | Nous Hermes 3 · function-calling models |
| 16 | MCP USB-C | `mcp.html` | Model Context Protocol · client + server |
| 17 | P1 Hardening | `p1-hardening.html` | + §8 cost-latency 5 case |
| 18 | Behavioral & Deep-Dive ★ | `behavioral.html` | Project deep-dive · STAR stories |
| 19 | Post-Training ★ | `post-training.html` | SFT / DPO / RLHF / RLAIF |
| 20 | Multimodal & Voice ★ | `multimodal-voice.html` | VLM · voice agent stack |
| 21 | Agent Benchmarks ★ | `benchmarks.html` | SWE-bench / TauBench / BFCL guide |
| 22 | 工业化基建 ★ | `industrial.html` | Plan mode / permissions / hooks / AGENTS.md / slash / 17 features total |
| 23 | Multi-Agent ★ | `multi-agent.html` | 5 patterns + 4 frameworks + "default don't" |
| 24 | From Demo to Public SaaS ★ | `saas.html` | 11-phase productisation · 50-item checklist |

★ = added during the 2026-05 sprint that this AI agent (or its predecessor)
worked on. They share the dark Petit-Prince palette and have a topbar nav
linking back to `index.html`, `industrial.html`, `multi-agent.html`,
`saas.html`, and the live `TaotaoAgent` link to `http://localhost:5180/`.

When you add a new book, update **3 places** in `docs/index.html`
(topbar nav, quick-start cards, all-books grid + coverage matrix) AND
the README, AND inject a cross-ref link into older books' nav.

---

## §8 · Major features (~35) · grouped

Group A · Core agent (graph backend)
- 13-node LangGraph DAG · perception → planner → supervisor → executor → critic
- Tool router (LLM-based selection)
- Reflection loop (critic re-routes back to executor)
- Guardrails (input/output safety)

Group B · Harness backend
- Single while-loop · Claude-Code-style
- Auto-compaction at token threshold
- Subagent dispatch (`dispatch_subagent` tool)
- Plan mode (`--plan` CLI · read-only context-gathering pass)
- Slash commands (`/compact /clear /plan /resume`)
- Session persistence (`~/.taotao/sessions/*.jsonl`)
- `--continue` / `--resume <id>` resume

Group C · Tools (12 built-in)
- `calculator`, `current_time`, `web_search` (Tavily+DDG), `python_repl`
- `file_ops` (read/write/list within jail), `propose_edit` (diff preview)
- `read_tool_result` (re-read truncated output)
- `memory_tool`, `profile_tool`, `skills_tool`
- `multi_agent_run` (debate/vote/handoff/reflection dispatcher)

Group D · Memory layers
- Short-term: window buffer + rolling summary
- Long-term: Chroma per-tenant collection (multi-tenant after Book 24)
- Profile: durable key=value
- Reflections: nightly self-reflection
- Skills: learned procedural snippets

Group E · MCP (dual-role)
- Server: expose our tools to other agents (Claude Desktop, Cursor, Cline)
- Client: call other MCP servers (config in `mcp_servers.example.json`)

Group F · Multi-agent (Book 23)
- `debate` · N agents argue until consensus + LLM-judge
- `voting` · self-consistency · N independent + majority
- `handoff` · OpenAI-Swarm-style explicit transfer
- `reflection` · actor-critic refinement loop

Group G · Industrial (Book 22)
- `permissions.py` · allow/ask/deny per-tool gate
- `hooks.py` · pre/post tool hooks
- `AGENTS.md` runtime context loader
- 3-tier settings (CLI args > env > file)
- Plan mode (`--plan`)
- Slash commands · diff preview · subagent-as-markdown

Group H · Production (Book 24 starter · this session)
- `auth/identity.py` · JWT + tenant resolver · 3-mode fallback
- Multi-tenant memory · per-tenant chroma collection · tested isolation
- `deploy/nginx/taotao.conf.example` · TLS + 5 security headers + SSE
- `frontend/Dockerfile.prod` · multi-stage build → nginx static

Group I · Observability
- Custom JSONL trace bus (`data/traces/`)
- OpenTelemetry (OTLP exporter)
- Prometheus `/metrics`
- Sentry (error aggregation)
- Token / cost guardrail (per-session budget)

Group J · Frontend
- Multi-session sidebar (resume past chats)
- Auto-generated TS types (`schema.gen.ts`)
- Streaming SSE rendering
- Markdown + syntax highlighting (highlight.js)
- Bidirectional cross-links to `docs/` tutorials

---

## §9 · Recent work · most recent first (2026-05)

| Date | Commit | What |
|---|---|---|
| 2026-05-13 | (this) | **P0 debt cleanup** · `LICENSE` (MIT) · ContextVar-based identity middleware in `app.py` (multi-tenancy now ACTUALLY enforced via `get_memory()`) · `backend/tests/` with 56 passing tests (auth + tenant isolation + tools + multi-agent + app HTTP) · ruff + mypy configured in `pyproject.toml` (scoped to new code) · `.github/workflows/test.yml` 3-job CI (backend lint+type+test · frontend build · docs sanity) · `frontend/src/vite-env.d.ts` to fix pre-existing TS error |
| 2026-05-13 | `83ca4ea` | Book 24 · From Demo to Public SaaS · 11-phase roadmap + 4 starter implementations (auth, multi-tenant memory, nginx prod, frontend Dockerfile.prod) |
| 2026-05-13 | `1c8b83a` | docs(diagrams): swap ASCII art for Mermaid · matches Petit-Prince palette |
| 2026-05-13 | `2420b35` | Book 23 + 4 multi-agent patterns + `multi_agent_run` tool |
| 2026-05-12 | `69a6310` | App ↔ Tutorial bidirectional crosslinks (TaotaoAgent floating link) |
| 2026-05-12 | `4218795` | Book 22 · 17 production features (industrial-grade · Claude-Code reverse-eng) |
| 2026-05-11 | `883e8b0` | Books 18-21 (behavioral / post-training / multimodal / benchmarks) + 3 patches |
| 2026-05-10 | (multiple) | Style iterations on the home page · landed on Petit-Prince dark theme |
| 2026-05-09 | `93c2d6e` | Book 17 P1 hardening + deep-dive sections |
| 2026-05-09 | `59b290d` | Multi-session sidebar |
| 2026-05-09 | `92eb82d` | OpenAPI → TypeScript type codegen |

The session that produced Books 22-24 also did:
- Multi-agent patterns implementation (`backend/agent/multi_agent/`)
- Industrial features (`permissions.py`, `hooks.py`, `slash_commands.py`,
  `subagents/loader.py`, `tools/read_tool_result.py`, `tools/skills_tool.py`)
- SaaS starters (`auth/`, multi-tenant memory, `deploy/nginx/`,
  `frontend/Dockerfile.prod`)
- 24-book reorganisation (8-phase learning path)

---

## §10 · Current state · what's done · what's not

### Tier-2 ready (internal SaaS · ~100 users)
- ✅ Auth dependency (JWT or API_KEY) · **wired via FastAPI middleware**
- ✅ Multi-tenant memory · ContextVar-bridged · `get_memory()` is now tenant-aware everywhere (graph nodes, harness tools, /memory endpoints) · **isolation tested**
- ✅ TLS + reverse-proxy config (template ready)
- ✅ Frontend production build (Dockerfile.prod)
- ✅ Per-session cost guardrail
- ✅ Permission gate · audit-able tool execution
- ✅ Hooks · slash commands · plan mode
- ✅ MCP dual-role
- ✅ Multi-agent patterns
- ✅ Sentry · OpenTelemetry · Prometheus
- ✅ **Tests** · 56 pytest cases covering auth/tenant/tools/multi-agent/app HTTP
- ✅ **CI** · ruff + mypy + pytest + frontend build + docs check on every push

### NOT yet done (Tier-3 public SaaS gaps · see Book 24 for the full list)

**P0 still open**:
1. Postgres / managed-Postgres migration (currently SQLite checkpoints)
2. Managed vector DB migration (currently local Chroma)
3. Sandbox for `python_repl` (currently subprocess-only · gVisor/Firecracker not wired)
4. Per-user (not per-session) daily/monthly token quota
5. Stripe billing integration
6. Secrets manager integration (currently `.env` files)

**P1 worth doing soon**:
7. CI/CD pipeline (GitHub Actions: test → build → canary)
8. Graceful shutdown drain
9. OTLP exporter to external sink (currently console)
10. RBAC (admin vs user) on `/admin/*` endpoints
11. Backup + restore drills

**P2 (Tier-3 only)**:
12. SOC2 Type II
13. Multi-region deployment
14. WAF / DDoS protection
15. Public status page
16. On-call rotation

**See `docs/saas.html` for the full 50-item checklist.**

---

## §11 · How to extend (cookbook)

### Add a new tool

1. Create `backend/agent/tools/your_tool.py`:
   ```python
   from langchain_core.tools import tool

   @tool
   def your_tool(arg: str) -> str:
       """One-line description shown to the LLM."""
       return result
   ```
2. Register in **both** registries:
   - `backend/agent/tools/registry.py` → `GRAPH_TOOLS.append(your_tool)`
   - `backend/agent/harness/tools.py` → `HARNESS_TOOLS.append(your_tool)`
3. (Optional) Add a permission policy in `backend/agent/permissions.py`
4. Smoke test:
   ```bash
   cd backend && source .venv/bin/activate
   python -c "from agent.harness.tools import HARNESS_TOOLS; print([t.name for t in HARNESS_TOOLS])"
   ```

### Add a new endpoint

1. Add to `backend/app.py`:
   ```python
   from agent.auth import current_identity, Identity

   @app.post("/your/route", tags=["yours"])
   async def your_route(
       payload: YourPayloadModel,
       ident: Identity = Depends(current_identity),
   ):
       # ident.tenant_id available for namespacing
       ...
   ```
2. Regenerate frontend types:
   ```bash
   cd frontend && npm run gen:api
   ```

### Add a new book / tutorial chapter

1. Create `docs/your-slug.html` · copy the structure from
   `docs/saas.html` (header, palette, mermaid loader, foot).
2. Update `docs/index.html` in **3 places**:
   - Topbar `.nav-links`
   - Quick-start cards section
   - All-books grid + coverage matrix
3. Update the stats numbers in the hero + footer (book count, line count).
4. Update `README.md` · add the row to the books list + the
   "Phase 8 / 学习路径" section.
5. Cross-link from older relevant books · grep for similar `<a href="..."` patterns.

### Add a new multi-agent pattern

1. Create `backend/agent/multi_agent/your_pattern.py` · use the
   `Result` and `AgentSpec` types from `types.py`.
2. Re-export from `backend/agent/multi_agent/__init__.py`.
3. Add a branch in `backend/agent/tools/multi_agent_tool.py` ·
   `multi_agent_run(pattern="your_pattern", ...)`.
4. Document in `docs/multi-agent.html` (add a section).

### Add a new MCP tool to expose externally

1. Already-`@tool`-decorated tools are auto-exposed by
   `backend/agent/mcp/server.py`. Just register the tool (above)
   and they show up over MCP automatically.

---

## §12 · Verification before claiming "done"

Run these in order before you tell the user a change is shipped:

```bash
# 1 · Backend imports clean (no syntax errors, no missing deps)
cd backend && source .venv/bin/activate
python -c "from app import app; print('app OK')"

# 2 · Tools all register
python -c "from agent.harness.tools import HARNESS_TOOLS; \
            print(len(HARNESS_TOOLS), 'tools registered')"

# 3 · Multi-tenant memory isolation (if you touched memory or auth)
python -c "
from agent.memory.long_term import LongTermMemory
a = LongTermMemory.for_tenant('a'); b = LongTermMemory.for_tenant('b')
a.remember('a-secret'); hits = (b.collection.query(query_texts=['secret'], n_results=5).get('documents') or [[]])[0]
assert 'a-secret' not in hits, 'TENANT LEAK'
print('isolation OK')
"

# 4 · Smoke chat (needs API key in .env or local Ollama)
curl -sS -X POST http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -H "X-API-Key: $API_KEY" \
  -d '{"message":"say hi"}' | head -c 200

# 5 · Eval harness (full · slow · only when you change tools/prompts)
python -m eval.cli --limit 5

# 6 · Frontend builds
cd ../frontend && npm run build      # tsc -b && vite build · should exit 0

# 7 · Lint (if you touched docs)
# Visit http://localhost:8000/tutorial/<your-page>.html · check it renders
```

If any of #1, #2, #6 fail · don't claim done · fix the regression first.

---

## §13 · Common gotchas

1. **Don't break the runtime `AGENTS.md`** · it's loaded at every chat.
   Edits change agent behaviour live.
2. **Tools must be registered in BOTH places** (graph + harness) or one
   backend silently misses them.
3. **`safe_run_tool` is mandatory** · don't call tool functions directly.
   The harness LLM relies on consistent truncation/offset metadata.
4. **Per-tenant memory is the SaaS data-isolation primitive** · if you
   `LongTermMemory()` (no arg) in a request handler that has user
   identity, you've created a data-leak path. Always
   `LongTermMemory.for_tenant(ident.tenant_id)`.
5. **Frontend types are auto-generated** · don't hand-edit
   `src/api/schema.gen.ts`. Run `npm run gen:api` after changing the
   backend's pydantic models.
6. **Docs use the dark Petit-Prince palette** · don't introduce a new
   color scheme · match the existing CSS variables (`--gold`, `--rust`,
   `--leaf`, `--sky`, `--plum`, `--paper`, `--ink`).
7. **Diagrams use Mermaid, not ASCII art** · include
   `docs/_assets/mermaid-init.html` content before `</body>`.
8. **Anthropic models are pinned** · don't suggest GPT/Gemini-only code
   paths unless the user explicitly switches model.
9. **`from __future__ import annotations`** is on most files · always
   safe to add, never required to remove.
10. **`data/` is gitignored** · contains chroma vectors + sqlite
    checkpoints + JSONL traces + HF cache. Delete to reset state.
11. **MCP server runs over stdio** (`python -m agent.mcp`) · not HTTP.
    The HTTP `app.py` is unrelated.
12. **`taotao_cli.py` is independent of the FastAPI server** · it
    instantiates the harness loop in-process. Don't assume the server
    needs to be running for CLI to work.

---

## §14 · Where to learn more

- **What is this thing?** → `README.md` · then `docs/learn.html` (Book 01)
- **How does each node work?** → `docs/implementation.html` (Book 02 walks
  the codebase node-by-node)
- **Two backends explained** → `docs/harness-vs-graph.html` (Book 14)
- **Production patterns we follow** → `docs/p1-hardening.html` (Book 17)
- **Industrial-grade features** → `docs/industrial.html` (Book 22)
- **Multi-agent patterns** → `docs/multi-agent.html` (Book 23)
- **Going to public SaaS** → `docs/saas.html` (Book 24)
- **All 24 books index** → `docs/index.html`

To browse the docs locally:
```bash
cd backend && source .venv/bin/activate
uvicorn app:app --port 8000
open http://localhost:8000/tutorial/index.html
```

---

## §15 · License & credits

MIT license · `LICENSE` at root.

This file is intentionally machine-readable but also human-readable ·
edits welcome. When you (an AI agent) make significant changes to the
project, append a row to §9 (Recent work) in the same format and update
§10 (Current state) if you've moved the needle on Tier-2/3 readiness.

When in doubt: **prefer making the existing books more right over
creating new ones** · the manual is dense already.

— Last updated 2026-05-13 by the agent that wrote Book 24.
