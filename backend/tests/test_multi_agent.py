"""Tests for `agent.multi_agent` shape & wiring.

We don't actually call any LLM here · those tests live in eval/ and need
real API keys.  These are pure-Python unit tests checking that:
  - The 4 patterns export the expected callables
  - The shared `Result` / `AgentSpec` shapes are stable
  - The `multi_agent_run` tool routes correctly without an LLM key
    (we just check it errors gracefully · i.e. doesn't crash with a
    KeyError on the dispatch dict)
"""
from __future__ import annotations

import inspect

import pytest

from agent.multi_agent import (
    AgentSpec,
    Result,
    run_debate,
    run_handoff,
    run_reflection,
    run_vote,
)


class TestSharedTypes:
    def test_result_has_required_fields(self):
        r = Result(final="answer")
        assert r.final == "answer"
        assert r.trace == []
        assert r.meta == {}

    def test_result_meta_independent_per_instance(self):
        """Mutable defaults bug check · each Result must get its own
        dict, not a class-level shared one."""
        r1 = Result(final="a")
        r2 = Result(final="b")
        r1.meta["key"] = 1
        assert "key" not in r2.meta

    def test_agent_spec_minimal(self):
        spec = AgentSpec(name="researcher", system="You research.")
        assert spec.name == "researcher"
        assert spec.system == "You research."
        assert spec.model is None  # optional


class TestPatternExports:
    @pytest.mark.parametrize("fn", [run_debate, run_vote, run_handoff, run_reflection])
    def test_pattern_callable(self, fn):
        assert callable(fn)

    @pytest.mark.parametrize("fn,required_param", [
        (run_debate, "task"),
        (run_vote, "task"),
        (run_handoff, "task"),
        (run_reflection, "task"),
    ])
    def test_pattern_takes_task_arg(self, fn, required_param):
        sig = inspect.signature(fn)
        assert required_param in sig.parameters, (
            f"{fn.__name__} missing required param: {required_param}"
        )


class TestMultiAgentTool:
    def test_tool_registered(self):
        from agent.tools.multi_agent_tool import multi_agent_run
        assert multi_agent_run.name == "multi_agent_run"
        assert "pattern" in multi_agent_run.args

    def test_unknown_pattern_handled(self):
        """Calling with a bogus pattern should produce an error string,
        not a KeyError crash."""
        from agent.tools.multi_agent_tool import multi_agent_run
        try:
            out = multi_agent_run.invoke({
                "pattern": "telekinesis",  # not real
                "task": "anything",
            })
        except Exception as e:
            # Acceptable · either errors out or returns error string
            assert "pattern" in str(e).lower() or "unknown" in str(e).lower()
            return
        # Or returns a failure marker
        assert "error" in str(out).lower() or "unknown" in str(out).lower()
