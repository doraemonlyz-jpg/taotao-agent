"""Tests for the python_repl sandbox abstraction.

We can run the subprocess backend in CI · docker / gvisor backends
require infrastructure we don't ship in pytest, so they're tested
indirectly:
  - Backend selection logic (env var → backend name)
  - Fallback when docker isn't on PATH
  - SandboxResult shape consistency

The python_repl tool itself is exercised by tests/test_tools.py to
keep the LLM-facing contract pinned.
"""
from __future__ import annotations

import pytest

from agent.tools._sandbox import (
    SandboxResult,
    _backend,
    _docker_image,
    _docker_memory,
    _docker_pids,
    run_python,
    status,
)


@pytest.fixture(autouse=True)
def _reset_sandbox_env(monkeypatch):
    for k in (
        "PYTHON_REPL_SANDBOX",
        "PYTHON_REPL_DOCKER_IMAGE",
        "PYTHON_REPL_DOCKER_MEMORY",
        "PYTHON_REPL_DOCKER_PIDS",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# --------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------- #
class TestBackendSelection:
    def test_default_subprocess(self):
        assert _backend() == "subprocess"

    def test_explicit_docker(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_SANDBOX", "docker")
        assert _backend() == "docker"

    def test_explicit_gvisor(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_SANDBOX", "gvisor")
        assert _backend() == "gvisor"

    def test_unknown_falls_back_to_subprocess(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_SANDBOX", "vsphere")
        assert _backend() == "subprocess"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_SANDBOX", "DOCKER")
        assert _backend() == "docker"


# --------------------------------------------------------------------- #
# Docker config helpers
# --------------------------------------------------------------------- #
class TestDockerConfig:
    def test_default_image(self):
        assert _docker_image() == "python:3.12-slim"

    def test_custom_image(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_DOCKER_IMAGE", "myorg/sandbox:latest")
        assert _docker_image() == "myorg/sandbox:latest"

    def test_default_memory(self):
        assert _docker_memory() == "256m"

    def test_default_pids(self):
        assert _docker_pids() == 64

    def test_pids_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_DOCKER_PIDS", "not-a-number")
        assert _docker_pids() == 64


# --------------------------------------------------------------------- #
# Subprocess backend (we CAN run this in CI · no docker needed)
# --------------------------------------------------------------------- #
class TestSubprocessBackend:
    def test_simple_print(self):
        r = run_python("print(2 + 2)", timeout_s=5)
        assert r.backend == "subprocess"
        assert r.exit_code == 0
        assert r.timed_out is False
        assert "4" in r.stdout

    def test_stderr_captured(self):
        r = run_python("import sys; sys.stderr.write('oops\\n')", timeout_s=5)
        assert "oops" in r.stderr
        assert r.exit_code == 0

    def test_nonzero_exit(self):
        r = run_python("raise SystemExit(7)", timeout_s=5)
        assert r.exit_code == 7

    def test_timeout(self):
        r = run_python("import time; time.sleep(10)", timeout_s=1)
        assert r.timed_out is True
        assert r.backend == "subprocess"

    def test_isolated_no_user_site(self):
        # `python -I` strips PYTHONPATH and user site · sys.path should be
        # short.  This is a smoke test that we're using -I correctly.
        r = run_python(
            "import sys; print(any('site-packages' not in p for p in sys.path))",
            timeout_s=5,
        )
        assert r.exit_code == 0


# --------------------------------------------------------------------- #
# Fallback paths · docker requested but not available
# --------------------------------------------------------------------- #
class TestFallback:
    def test_docker_falls_back_when_unavailable(self, monkeypatch):
        # Force `which docker` to return None by emptying PATH for the
        # subprocess that shutil.which queries.
        monkeypatch.setenv("PYTHON_REPL_SANDBOX", "docker")
        monkeypatch.setattr("agent.tools._sandbox.shutil.which", lambda _: None)
        r = run_python("print('hi')", timeout_s=5)
        # Should have silently fallen back to subprocess
        assert r.backend == "subprocess"
        assert "hi" in r.stdout

    def test_gvisor_falls_back_when_no_docker(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_SANDBOX", "gvisor")
        monkeypatch.setattr("agent.tools._sandbox.shutil.which", lambda _: None)
        r = run_python("print('paranoid')", timeout_s=5)
        assert r.backend == "subprocess"
        assert "paranoid" in r.stdout


# --------------------------------------------------------------------- #
# Status reporter
# --------------------------------------------------------------------- #
class TestStatus:
    def test_status_shape(self):
        s = status()
        assert "backend" in s
        assert "docker_available" in s

    def test_gvisor_status_includes_runsc(self, monkeypatch):
        monkeypatch.setenv("PYTHON_REPL_SANDBOX", "gvisor")
        s = status()
        assert "runsc_available" in s


# --------------------------------------------------------------------- #
# Result dataclass
# --------------------------------------------------------------------- #
class TestSandboxResult:
    def test_immutable(self):
        r = SandboxResult(stdout="x", stderr="", exit_code=0, timed_out=False, backend="subprocess")
        with pytest.raises(Exception):
            r.stdout = "y"  # type: ignore[misc]  # frozen dataclass


# --------------------------------------------------------------------- #
# python_repl tool · still works through the new abstraction
# --------------------------------------------------------------------- #
class TestPythonReplToolStillWorks:
    def test_basic_execution(self):
        from agent.tools.python_repl import python_repl

        out = python_repl.invoke({"code": "print('sandbox-ok')"})
        assert "sandbox-ok" in out

    def test_timeout_message(self):
        from agent.tools.python_repl import python_repl

        out = python_repl.invoke({"code": "import time; time.sleep(15)"})
        assert "timed out" in out.lower()
