<!-- Thanks for sending a PR! Tick boxes as you go ·  unticked = fine, just visible. -->

## What & why

<!-- One paragraph · what changes, why now. Link to issue if any: closes #N -->

## How to test

<!-- Repro the change locally · 3-5 lines. Reviewer copy-pastes and gets to green. -->

```bash
# e.g.
cd backend && .venv/bin/pytest tests/test_my_change.py -q
```

## Touched

<!-- Which subsystems? Helps reviewers route. -->

- [ ] Graph backend (`agent/nodes/`, `agent/graph.py`)
- [ ] Harness backend (`agent/harness/`)
- [ ] Tools registry (`agent/tools/`)
- [ ] Memory layer (`agent/memory/`)
- [ ] Auth / multi-tenancy (`agent/auth/`)
- [ ] MCP (client or server)
- [ ] Multi-agent patterns (`agent/multi_agent/`)
- [ ] Frontend (`frontend/src/`)
- [ ] Docs / books (`docs/`)
- [ ] CI / DX / config

## Checklist

<!-- Skip checks that don't apply ·  but say so in the description. -->

- [ ] Pytest green locally · `cd backend && .venv/bin/pytest tests/`
- [ ] Ruff green · `.venv/bin/ruff check agent/auth agent/multi_agent tests`
- [ ] Mypy green · `.venv/bin/mypy`
- [ ] Frontend builds · `cd frontend && npm run build`
- [ ] If new tools added · registered in BOTH `tools/registry.py` AND `harness/tools.py`
- [ ] If multi-tenant data path · isolation tested (see `tests/test_memory_tenant.py`)
- [ ] If new endpoint · uses `Depends(current_identity)` or `Depends(require_api_key)`
- [ ] If new book · added to `docs/index.html` (3 places) AND `README.md` AND cross-linked from related books
- [ ] CHANGELOG / release notes not needed (we're pre-1.0)
