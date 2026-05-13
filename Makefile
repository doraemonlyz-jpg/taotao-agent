# taotao-agent · one Makefile to rule the dev loop.
#
# Common pitfalls:
# - Make uses TAB indentation. Don't paste spaces.
# - Each recipe runs in its own shell · `cd` doesn't persist between lines.
# - .PHONY targets aren't files · without it `make test` would no-op if a file named "test" exists.

.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND   := backend
FRONTEND  := frontend
PY        := $(BACKEND)/.venv/bin/python
PIP       := $(BACKEND)/.venv/bin/pip
PYTEST    := $(BACKEND)/.venv/bin/pytest
RUFF      := $(BACKEND)/.venv/bin/ruff
MYPY      := $(BACKEND)/.venv/bin/mypy
UVICORN   := $(BACKEND)/.venv/bin/uvicorn

# Treat anything with a colon as a target · don't try to be a "real" file.
.PHONY: help install install-backend install-frontend dev dev-backend dev-frontend cli \
        test test-backend test-frontend lint lint-backend lint-backend-all lint-frontend \
        format typecheck build build-backend build-frontend \
        docs docs-serve clean clean-backend clean-frontend \
        docker-up docker-down docker-logs codegen \
        check ship

# ---------- help ----------
help:  ## show this help (default)
	@echo "taotao-agent · common dev commands"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------- install ----------
install: install-backend install-frontend  ## install backend + frontend deps

install-backend:  ## create .venv and install backend deps
	cd $(BACKEND) && uv sync --extra dev

install-frontend:  ## install frontend deps
	cd $(FRONTEND) && npm install

# ---------- dev (run two terminals · or use `make dev-backend` and `make dev-frontend` separately) ----------
dev:  ## run backend + frontend concurrently (needs `npm install -g concurrently` or use the two split targets)
	@command -v concurrently >/dev/null 2>&1 || { echo "Install \`concurrently\` or use: make dev-backend / make dev-frontend in two terminals"; exit 1; }
	concurrently -n backend,frontend -c blue,green "make dev-backend" "make dev-frontend"

dev-backend:  ## run FastAPI on :8000 with reload
	cd $(BACKEND) && ../$(UVICORN) app:app --reload --host 0.0.0.0 --port 8000

dev-frontend:  ## run Vite dev server on :5173
	cd $(FRONTEND) && npm run dev

cli:  ## run interactive CLI (Rich UI · same agent as /chat)
	cd $(BACKEND) && ../$(PY) taotao_cli.py

# ---------- test ----------
test: test-backend  ## run all tests (backend only · frontend has no tests yet)

test-backend:  ## pytest with quiet output
	cd $(BACKEND) && ../$(PYTEST) tests/ -q

test-frontend:  ## frontend tests (placeholder · no test runner yet)
	@echo "frontend has no tests yet · skipping"

# ---------- lint / format / typecheck ----------
lint: lint-backend lint-frontend  ## lint everything

lint-backend:  ## ruff check (same scope as CI · narrow to typed surfaces)
	cd $(BACKEND) && ../$(RUFF) check agent/auth agent/multi_agent tests

lint-backend-all:  ## ruff check entire backend (legacy code · expect noise)
	cd $(BACKEND) && ../$(RUFF) check agent tests app.py

lint-frontend:  ## frontend lint (vite tsc)
	cd $(FRONTEND) && npm run lint 2>/dev/null || npx tsc --noEmit

format:  ## ruff format (auto-fix backend, narrow CI scope)
	cd $(BACKEND) && ../$(RUFF) format agent/auth agent/multi_agent tests
	cd $(BACKEND) && ../$(RUFF) check --fix agent/auth agent/multi_agent tests

typecheck:  ## mypy on typed surface (auth + multi_agent.types)
	cd $(BACKEND) && ../$(MYPY)

check: lint typecheck test  ## CI gate · same as GitHub Actions

# ---------- build ----------
build: build-backend build-frontend  ## production build

build-backend:  ## verify backend imports cleanly (no actual artifact · just smoke)
	cd $(BACKEND) && ../$(PY) -c "import app, agent.graph, agent.harness; print('backend ok')"

build-frontend:  ## vite build → frontend/dist/
	cd $(FRONTEND) && npm run build

# ---------- docs ----------
docs:  ## list all 24 books
	@ls -1 docs/*.html | grep -v _assets | nl

docs-serve:  ## serve docs at http://localhost:8080
	cd docs && python3 -m http.server 8080

# ---------- codegen ----------
codegen:  ## regenerate frontend TS types from backend OpenAPI
	cd $(FRONTEND) && npm run codegen

# ---------- docker ----------
docker-up:  ## start full stack (backend + frontend) via docker-compose
	docker compose up -d --build

docker-down:  ## stop the stack
	docker compose down

docker-logs:  ## tail logs
	docker compose logs -f --tail=200

# ---------- ship (Phase X 收官 helper) ----------
ship: check  ## run full check + commit (intended manual review of the diff first)
	@echo "✓ all checks passed · review your diff then `git commit`"

# ---------- clean ----------
clean: clean-backend clean-frontend  ## remove all caches and build artifacts

clean-backend:
	rm -rf $(BACKEND)/__pycache__ $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(BACKEND)/.mypy_cache
	find $(BACKEND) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

clean-frontend:
	rm -rf $(FRONTEND)/dist $(FRONTEND)/.vite
