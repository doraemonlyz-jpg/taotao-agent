"""Pluggable sandbox backends for `python_repl`.

Three backends available, gated by `PYTHON_REPL_SANDBOX` env var:

  - subprocess (default · 0 ops · NOT secure for hostile input)
      Runs in a forked subprocess with `python -I` (no site, no env).
      Fine for trusted internal demos, NOT for public SaaS.

  - docker (production · isolated container · network off)
      Spins up a one-shot `python:3.12-slim` container per call:
        --read-only --network=none --memory=256m --pids-limit=64
        --cap-drop=ALL --user=nobody --tmpfs=/tmp:rw,size=10m
      Container is reaped after the timeout.  Adds ~300ms cold start.
      Survives malicious filesystem writes / fork bombs / network exfil.

  - gvisor (paranoid · same as docker + --runtime=runsc)
      gVisor's Sentry intercepts every syscall in user-space · stops
      kernel exploits cold.  Recommended for any python-from-LLM
      execution path facing the public internet.  Adds ~600ms cold
      start (Sentry init).  Requires gVisor installed on the host:
      https://gvisor.dev/docs/user_guide/install/

Why not Firecracker / Kata: they're orders of magnitude harder to set
up than gVisor (Firecracker needs KVM + a curated rootfs · Kata needs
a runtime swap and dedicated kernel).  gVisor is `apt install
runsc` + `--runtime=runsc` on docker.

Why not E2B / Modal: they're great managed offerings but lock you in.
This sandbox keeps you in control · swap to E2B by writing a fourth
backend.

Output contract:
  Return a `SandboxResult` dataclass with stdout / stderr / exit_code
  / timed_out · the caller (python_repl tool) formats the LLM-facing
  string.  This separation means future tools can reuse the sandbox.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass

log = logging.getLogger("agent.tools.sandbox")


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    backend: str


# --------------------------------------------------------------------- env
def _backend() -> str:
    """`PYTHON_REPL_SANDBOX` · subprocess (default) | docker | gvisor."""
    raw = (os.environ.get("PYTHON_REPL_SANDBOX") or "subprocess").strip().lower()
    if raw not in {"subprocess", "docker", "gvisor"}:
        log.warning("Unknown PYTHON_REPL_SANDBOX=%r · falling back to subprocess", raw)
        return "subprocess"
    return raw


def _docker_image() -> str:
    """`PYTHON_REPL_DOCKER_IMAGE` · pre-pull this for a faster cold start.
    Default: python:3.12-slim · ~50MB."""
    return (os.environ.get("PYTHON_REPL_DOCKER_IMAGE") or "python:3.12-slim").strip()


def _docker_memory() -> str:
    return (os.environ.get("PYTHON_REPL_DOCKER_MEMORY") or "256m").strip()


def _docker_pids() -> int:
    raw = os.environ.get("PYTHON_REPL_DOCKER_PIDS") or "64"
    try:
        return int(raw)
    except ValueError:
        return 64


# --------------------------------------------------------------------- pub
def run_python(code: str, *, timeout_s: int) -> SandboxResult:
    """Execute `code` in the configured sandbox backend.

    The function picks the backend per-call (env can be flipped in
    /admin in the future) so a deploy doesn't need a restart to swap.

    Falls back to `subprocess` when the requested backend's
    prerequisites are missing · we always log loudly and emit a
    metric in the future (TODO).
    """
    backend = _backend()
    wrapped = textwrap.dedent(code)

    if backend == "docker":
        if not shutil.which("docker"):
            log.warning("PYTHON_REPL_SANDBOX=docker but `docker` not on PATH · falling back")
            return _run_subprocess(wrapped, timeout_s)
        return _run_docker(wrapped, timeout_s, runtime=None)

    if backend == "gvisor":
        if not shutil.which("docker"):
            log.warning("PYTHON_REPL_SANDBOX=gvisor needs docker · falling back")
            return _run_subprocess(wrapped, timeout_s)
        return _run_docker(wrapped, timeout_s, runtime="runsc")

    return _run_subprocess(wrapped, timeout_s)


# --------------------------------------------------------------------- impl
def _run_subprocess(code: str, timeout_s: int) -> SandboxResult:
    """The legacy backend · `python -I` in a subprocess.

    Provides isolation from the parent process state but NOT from
    the host filesystem or network.  Acceptable for trusted dev.
    """
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            stdout="", stderr="", exit_code=-1,
            timed_out=True, backend="subprocess",
        )
    except Exception as e:
        return SandboxResult(
            stdout="", stderr=f"subprocess crashed: {e!r}",
            exit_code=-2, timed_out=False, backend="subprocess",
        )
    return SandboxResult(
        stdout=r.stdout or "",
        stderr=r.stderr or "",
        exit_code=r.returncode,
        timed_out=False,
        backend="subprocess",
    )


def _run_docker(code: str, timeout_s: int, *, runtime: str | None) -> SandboxResult:
    """Spin up a one-shot container · runs `python -I -c <code>`.

    Hardening flags applied:
      --rm                     · reap on exit
      --read-only              · root fs immutable
      --network=none           · no internet exfil
      --memory=256m            · OOM-kill on memory bombs
      --pids-limit=64          · stop fork bombs
      --cap-drop=ALL           · no caps · no ptrace, no chroot, etc.
      --user=nobody            · drop root
      --tmpfs=/tmp:rw,size=10m · scratch space for test files
      --runtime=runsc (gvisor) · syscall interception via Sentry

    The code is passed via an in-memory tempfile mount-bound to /tmp/in.py
    instead of the command line · keeps very long inputs working
    around the OS argv length limit (typically 128KB on Linux).
    """
    backend_name = "gvisor" if runtime == "runsc" else "docker"
    image = _docker_image()
    mem = _docker_memory()
    pids = _docker_pids()

    # Stash the code in a host tempfile · mount as read-only into /tmp/in.py
    # so even a hostile script can't rewrite its own source.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="taotao-sbx-",
    ) as f:
        f.write(code)
        host_path = f.name
    try:
        cmd = [
            "docker", "run", "--rm",
            "--read-only",
            "--network=none",
            f"--memory={mem}",
            f"--pids-limit={pids}",
            "--cap-drop=ALL",
            "--user=nobody",
            "--tmpfs=/tmp:rw,size=10m",
            "-v", f"{host_path}:/tmp/in.py:ro",
        ]
        if runtime:
            cmd += ["--runtime", runtime]
        cmd += [image, "python", "-I", "/tmp/in.py"]

        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout_s + 5,  # +5s for docker cold start
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                stdout="", stderr="", exit_code=-1,
                timed_out=True, backend=backend_name,
            )
        except Exception as e:
            return SandboxResult(
                stdout="", stderr=f"docker invocation failed: {e!r}",
                exit_code=-2, timed_out=False, backend=backend_name,
            )

        return SandboxResult(
            stdout=r.stdout or "",
            stderr=r.stderr or "",
            exit_code=r.returncode,
            timed_out=False,
            backend=backend_name,
        )
    finally:
        try:
            os.unlink(host_path)
        except OSError:
            pass


def status() -> dict:
    """Report the active backend + its readiness · for /health."""
    backend = _backend()
    out: dict[str, str | bool] = {"backend": backend}
    out["docker_available"] = bool(shutil.which("docker"))
    if backend == "gvisor":
        # `runsc --version` returns 0 if installed.  Tolerate missing
        # binary · we already log on first invocation.
        out["runsc_available"] = bool(shutil.which("runsc"))
    return out
