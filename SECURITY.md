# Security Policy

This project is a teaching / reference implementation of an agent stack. It is
**not** intended to be deployed publicly without the hardening described in
[Book 24 · From Demo to Public SaaS](docs/saas.html). If you find a
vulnerability anyway, we'd still like to know — quietly.

## Reporting a vulnerability

Please **don't** open a public GitHub issue.

Use one of these private channels:

1. **Preferred** · Open a [private security advisory](https://github.com/doraemonlyz-jpg/taotao-agent/security/advisories/new)
   on the repo. Only maintainers see it until disclosure.
2. Email the maintainers (see commit history for current contact).

Please include:

- Affected version / commit
- Reproduction steps (curl / payload / scenario)
- Expected vs actual behavior
- Suggested mitigation if you have one

We aim to:

- Acknowledge within **3 business days**
- Provide a status update within **10 business days**
- Ship a fix on `main` and credit you, if you'd like, in the Hall of Fame below

## Scope

In scope:

- Authentication / authorization bypass (`agent/auth/`)
- Tenant isolation breaks (`agent/memory/`, the identity middleware in `app.py`)
- Tool sandbox escape (`python_repl`, `bash_exec` · these run user code)
- Prompt injection that leaks secrets the agent shouldn't have access to
- Supply chain (deps, MCP servers we ship)

Out of scope:

- Findings that require root on the host
- Anything in `docs/` (it's static HTML · no runtime)
- DoS via expensive prompt (we have token-budget guardrails · see Book 17)
- Findings against unmaintained branches

## Hall of fame

Researchers who reported valid issues, with permission, will be listed here.

_(empty so far · be the first)_
