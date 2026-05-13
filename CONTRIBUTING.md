# Contributing to taotao-agent

> 欢迎 PR / issue / 嘴炮（友善的）。
> 本仓库的主旨是「**生产形态、能 hand-off、足以做面试 demo**」的 agent reference
> implementation —— 一切优化都围绕这三件事展开。

## TL;DR

```bash
git clone https://github.com/doraemonlyz-jpg/taotao-agent.git
cd taotao-agent
make install        # backend (uv) + frontend (npm)
make test           # 152+ pytest, ~10s
make lint           # ruff + mypy + prettier
make dev            # backend :8000 + frontend :5180
```

第一次提交前请执行 `pre-commit install`，钩子会在 commit 时自动 ruff format /
prettier / gitleaks。CI 会跑同一组检查 + frontend build，所以本地通过 = 远端通过。

---

## 目录

1. [行为准则](#1-行为准则)
2. [仓库结构 60 秒导览](#2-仓库结构-60-秒导览)
3. [开发环境](#3-开发环境)
4. [运行时与配置](#4-运行时与配置)
5. [代码规范](#5-代码规范)
6. [测试](#6-测试)
7. [提交规范 + commit message](#7-提交规范--commit-message)
8. [PR 流程](#8-pr-流程)
9. [新增功能的 checklist](#9-新增功能的-checklist)
10. [发布 & 版本号](#10-发布--版本号)
11. [安全报告](#11-安全报告)

---

## 1. 行为准则

请阅读并遵守 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)（Contributor Covenant 2.1）。
不友善的评论会被删除，重复违反会被 ban。issue / PR 里请就事论事、对代码不对人。

---

## 2. 仓库结构 60 秒导览

```
agent-demo/
├── backend/                      # Python 3.12 · FastAPI + LangGraph
│   ├── app.py                    # 入口（已瘦身，主要是 router 装配）
│   ├── routers/                  # FastAPI APIRouter（chat / memory / admin / billing …）
│   ├── agent/
│   │   ├── graph.py              # LangGraph 13-node graph
│   │   ├── harness/              # while-loop 风格的 harness（Claude-Code 形态）
│   │   ├── multi_agent/          # 多 agent 4 种模式（hierarchical / debate / vote / handoff）
│   │   ├── tools/                # 工具注册表 + safe_run_tool
│   │   ├── memory/               # 短/长期记忆、profile、skill
│   │   ├── auth/                 # JWT/API_KEY/Identity/RBAC
│   │   ├── observability/        # OTel + Prom + structured logging
│   │   ├── billing/              # Stripe metered billing skeleton
│   │   └── quota.py              # 每用户 token/USD 配额
│   └── tests/                    # pytest，152+ 用例
├── frontend/                     # React 19 + Vite + TypeScript
│   ├── src/
│   │   ├── App.tsx               # 顶层
│   │   ├── components/           # ChatPanel / TracePanel / LoginPanel / AuthGate …
│   │   ├── api.ts                # 自动注入 auth header 的 fetch wrapper
│   │   └── auth.ts               # 客户端认证（API_KEY / JWT）
│   └── vite.config.ts            # 已配 code-splitting
├── docs/                         # 24 本书 · 40000+ 行 · senior 面试覆盖
├── clients/go-client/            # Go SDK（仅 stdlib）
├── deploy/                       # Nginx / docker / Postgres 配置示例
├── assets/                       # README 资源（screenshot / 图标）
├── README.md                     # 顶层 + 架构图 + screenshot
├── CLAUDE.md                     # 给 AI coding agent 的项目上下文
├── AGENTS.md                     # 注入 system prompt 的 agent 行为约定
├── DOCKER.md                     # 容器化指南
├── Makefile                      # install / dev / test / lint / build …
└── pyproject.toml + .pre-commit-config.yaml + .editorconfig + .gitattributes
```

> 想看每个文件夹的「为什么这么设计」→ 翻 `docs/implementation.html`（graph 版）
> 或 `docs/harness.html`（harness 版）。

---

## 3. 开发环境

### 必需

| 依赖 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.12 | backend 用 `uv`（`pip install uv`） |
| Node.js | 22.x | frontend；推荐 `nvm install 22` |
| Git | 2.30+ | LFS 不需要 |
| make | 任意 | 常用任务封装 |

### 可选

- **Docker / Docker Compose** —— `make docker` / `make compose`
- **gVisor (`runsc`)** —— `python_repl` 沙箱最高级
- **Postgres 16** —— `docker compose up postgres`，LangGraph checkpointer 升级用
- **pre-commit** —— `pip install pre-commit && pre-commit install`

### 一键起

```bash
make install        # uv sync backend, npm i frontend
cp backend/.env.example backend/.env
# 至少填一个 LLM key：OPENAI_API_KEY 或 ANTHROPIC_API_KEY
make dev            # 起 backend + frontend，浏览器开 http://localhost:5180
```

---

## 4. 运行时与配置

所有可调项都在 `backend/.env.example`，分组列出：

- **核心** · `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `MODEL_NAME`
- **认证** · `API_KEY` / `JWT_SECRET` / `ADMIN_USERS`
- **配额** · `QUOTA_ENABLED` / `QUOTA_DAILY_TOKENS` / `QUOTA_MONTHLY_USD`
- **存储** · `DATABASE_URL`（Postgres，可选）/ `CHROMA_PATH`
- **沙箱** · `PYTHON_REPL_SANDBOX=docker|subprocess|gvisor`
- **可观测性** · `OTEL_EXPORTER_OTLP_*`（gRPC / HTTP/protobuf 都支持）
- **计费** · `STRIPE_API_KEY` / `STRIPE_WEBHOOK_SECRET`

> **不要把 `.env` 提交到仓库**。`.gitignore` 已挡，`gitleaks` pre-commit 钩子会再
> 兜底一次。

---

## 5. 代码规范

### Python (backend)

- **格式化** · `ruff format`（line length 100，single quotes 关闭，preview 开）
- **lint** · `ruff check`（`E,F,I,UP,B,C4,SIM` 默认开，详见 `pyproject.toml`）
- **类型** · 新代码必须能过 `mypy backend`（已开 `strict_optional` + `disallow_untyped_defs`，对老代码用 `# type: ignore[reason]` 标注，**不要**全文件加 `# mypy: ignore-errors`）
- **导入** · 不要用 wildcard import；ruff 会按 isort 规则自动排序
- **文件头** · 不需要版权头；docstring 用 Google style，简短即可
- **error message** · 给 LLM 看的错误请保持 actionable（`docs/tool-design.html#ch-7`）

```python
# ✅ Good
def fetch_user(user_id: str) -> User:
    """Return the User row, or raise NotFound."""

# ❌ Bad
def fetchUser(uid):  # camelCase + 缺类型 + 没 docstring
    ...
```

### TypeScript (frontend)

- **格式化** · prettier（仓库根的 `.prettierrc` 由 pre-commit 触发）
- **lint** · `npm run lint`（`eslint` + `tsc --noEmit`）
- **风格** · 函数式组件 + hooks，不写 class component；样式优先用 CSS module，不用 styled-components
- **state** · 优先 `useState` / `useReducer`，跨页面才考虑 context；不引入 Redux

### Markdown / 文档

- **章节编号** · 用 `## §N · 标题`（与 `docs/*.html` 风格统一）
- **代码块** · 必带语言标签
- **图** · 一律用 mermaid，不写 ASCII art（参考 `docs/multi-agent.html`）

---

## 6. 测试

### 后端

```bash
make test                     # 跑全部 152+ 用例
pytest backend/tests/test_admin_rbac.py -k tenant -vv   # 单文件 + 关键字
pytest backend/tests/test_quota.py --pdb                # 失败时进 pdb
```

测试约定：

- **hermetic** —— 每个测试自带 `tmp_path` / fixture 清库；不能依赖跑序
- **fast** —— 整套 < 30s；任何 > 1s 的用例必须 `@pytest.mark.slow`
- **no real LLM** —— LLM 调用走 `tests/_fakes.py` 的 `FakeChatModel`
- **no real network** —— `pytest-socket` 已开（如果你新加测试需要网络，请 `--allow-hosts=localhost`）

### 前端

```bash
cd frontend
npm test         # vitest（如有）
npm run build    # 必须能过且不超 chunk size warning
```

### CI

GitHub Actions（`.github/workflows/test.yml`）会在每个 PR 上跑：

1. `ruff check` + `ruff format --check`
2. `mypy backend`
3. `pytest backend/tests`
4. `cd frontend && npm ci && npm run lint && npm run build`

CI 红 = PR 不能 merge。

---

## 7. 提交规范 + commit message

### 提交粒度

一个 commit 一个意图。reformat / rename / 实质改动分开，方便 reviewer + bisect。

### message 格式

参照 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>

<body, optional, wrap at 80 cols>

<footer, optional, e.g. BREAKING CHANGE / refs / co-authored-by>
```

`type` 用：

| type | 何时 |
|---|---|
| `feat` | 新功能 |
| `fix` | bug |
| `docs` | 只动文档 |
| `refactor` | 不改行为的重构 |
| `perf` | 性能优化 |
| `test` | 只改测试 |
| `chore` | 构建 / 工具 / CI |
| `style` | 格式化 |
| `revert` | 回滚 |

`scope` 例：`backend`, `frontend`, `routers/chat`, `multi_agent`, `docs/multi-agent`。

例子：

```
feat(routers/chat): add /chat/v3 with streaming reasoning blocks
fix(quota): reset_for_user must commit before snapshot
docs(multi-agent): clarify when *not* to use multi-agent (5 anti-patterns)
chore(ci): bump actions/checkout to v4
```

---

## 8. PR 流程

1. **fork + branch** —— `git checkout -b feat/awesome-thing`
2. **写代码 + 测试 + 文档**
3. **本地通过** · `make lint && make test && make build`
4. **push + 开 PR** —— 走 `.github/PULL_REQUEST_TEMPLATE.md`，把 checkbox 都打钩
5. **等 CI 绿 + 1 个 maintainer review**
6. **squash merge**（仓库默认策略，message 用 PR title）

### Review 你能期待的反馈

- API 设计是否兼容 `chat / memory / admin` 现有约定
- 是否新增了不必要的依赖（任何 npm/pip 包都要在 PR 里说明理由）
- 是否破坏了「graph 引擎 / harness 引擎」二选一的对称性
- 文档是否同步更新（24 本书的索引 + AGENTS.md）

### 你不需要担心的事

- **格式化** · pre-commit + CI 会兜底
- **签 CLA** · 本仓库不要求
- **issue 模板** · 自己 free-form 提也行，模板只是引导

---

## 9. 新增功能的 checklist

每加一个功能，请逐项核对，避免成为下一个「死代码」（参考 `CLAUDE.md` 的「常见
死代码陷阱」）：

- [ ] 在 `backend/agent/` 或 `routers/` 下有实现
- [ ] 在 `app.py` 或对应 router 注册（不只是定义函数 / 类）
- [ ] 有 `pytest` 用例，覆盖正例 + 至少 1 个反例
- [ ] 在 `backend/.env.example` 加了配置项 + 注释
- [ ] 在 `README.md` 或 `docs/*.html` 至少一处可被搜到
- [ ] 如果引入新依赖：在 PR description 解释 why，并跑过 `pip-audit` / `npm audit`
- [ ] 如果 schema 改变：跑 `npm run gen-types` 更新前端 TS 类型
- [ ] 如果是 long-running / async：加 OTel span + structured log 字段
- [ ] 如果涉及成本：加 `usage.add()` 调用，能被 `/usage/me` 看到

---

## 10. 发布 & 版本号

- 主分支始终可部署。`v0.x` 期间不保证 API 稳定，但会在 `CHANGELOG.md` 标注 BREAKING。
- tag 格式 `v<MAJOR>.<MINOR>.<PATCH>`（语义化版本）
- 镜像走 `ghcr.io/doraemonlyz-jpg/taotao-agent:<tag>`（CI 自动 push）

---

## 11. 安全报告

**不要在公开 issue 里贴漏洞**。

请按 [`/.well-known/security.txt`](.well-known/security.txt) 里的邮箱直接联系
maintainer。我们承诺：

- 24h 内回执
- 7 天内给出修复时间表
- 公布 CVE 前通知报告者，并在 `SECURITY.md` 致谢

---

## 谢谢 ✿

每一个 PR / issue / typo fix / mermaid 图美化都会让下一个 senior agent
engineer 候选人少踩一个坑。Welcome aboard.

—— taotao maintainers
