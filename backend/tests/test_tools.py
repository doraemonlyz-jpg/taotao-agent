"""Smoke tests for the built-in tool registry.

We don't try to test every tool's full behaviour · we just verify they
register, expose schemas, and the deterministic ones return sensible
output.  Behaviour-heavy tools (web_search, python_repl) are tested
manually + via eval/.
"""
from __future__ import annotations

from agent.harness.tools import HARNESS_TOOLS
from agent.tools.calculator import calculator
from agent.tools.current_time import current_time


class TestRegistryShape:
    def test_at_least_n_tools_registered(self):
        assert len(HARNESS_TOOLS) >= 8, (
            f"Only {len(HARNESS_TOOLS)} tools · we shipped 12+. "
            "Did someone delete the registry?"
        )

    def test_all_tools_have_unique_names(self):
        names = [t.name for t in HARNESS_TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"

    def test_critical_tools_present(self):
        names = {t.name for t in HARNESS_TOOLS}
        # These four are load-bearing · removing any breaks demos
        for must in ("calculator", "current_time", "remember", "recall"):
            assert must in names, f"Missing must-have tool: {must}"

    def test_multi_agent_tool_registered(self):
        names = {t.name for t in HARNESS_TOOLS}
        assert "multi_agent_run" in names

    def test_all_tools_have_descriptions(self):
        """LLMs depend on these descriptions · empty docstring = the LLM
        can't decide when to call the tool."""
        for t in HARNESS_TOOLS:
            assert t.description and len(t.description.strip()) >= 10, (
                f"Tool {t.name} has empty/trivial description"
            )


class TestCalculatorTool:
    def test_basic_arithmetic(self):
        result = calculator.invoke({"expression": "2 + 2"})
        assert "4" in str(result)

    def test_handles_floats(self):
        result = calculator.invoke({"expression": "1.5 * 4"})
        assert "6" in str(result)

    def test_rejects_dangerous_input(self):
        """numexpr should reject __import__ / exec / open expressions
        (return an error string · NOT actually execute them)."""
        try:
            result = calculator.invoke({"expression": "__import__('os').system('echo X')"})
        except Exception:
            return  # raising is also an acceptable rejection
        s = str(result).lower()
        # Result must signal rejection · pure success would lack any of these
        assert any(marker in s for marker in (
            "error", "invalid", "forbidden", "syntax", "cannot",
            "calculator", "could not",
        )), f"Calculator did not reject dangerous input · got: {result!r}"


class TestCurrentTimeTool:
    def test_returns_iso_format(self):
        out = current_time.invoke({})
        s = str(out)
        # Loose ISO 8601 sniff · 4-digit year + 2-digit month
        assert "20" in s and ":" in s
