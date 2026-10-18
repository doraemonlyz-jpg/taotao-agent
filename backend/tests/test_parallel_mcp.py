"""Exercise the opt-in preset through the real MCP adapter and HTTP transport.

Only HTTP I/O and the model are scripted; schema discovery, tool conversion,
permission gates, safe execution and result consumption use production code.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent import permissions
from agent.mcp import client
from agent.tools import safe_exec

SEARCH = "mcp__parallel_web_search"
FETCH = "mcp__parallel_web_fetch"
URL = "https://search.parallel.ai/mcp"
SOURCE = "https://www.python.org/downloads/"


@pytest.fixture
def mcp_http(monkeypatch, tmp_path):
    for key in (
        "MCP_CLIENT_CONFIG",
        "MCP_PARALLEL_SEARCH_ENABLED",
        "PARALLEL_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(client, "_CACHED", None)
    monkeypatch.setattr(client, "_STATUS", {"enabled": False, "servers": {}, "tool_count": 0})
    monkeypatch.setattr(permissions, "_global_path", lambda: tmp_path / "global.json")
    monkeypatch.setattr(permissions, "_project_path", lambda: tmp_path / "project.json")
    monkeypatch.setattr(permissions, "_RUNTIME_RULES", [])
    safe_exec.cache_clear()
    state = SimpleNamespace(requests=[], failure=None)

    async def send(self, request, **kwargs):
        assert str(request.url) == URL
        assert request.headers["user-agent"] == "taotao-agent/0.1.0"
        assert "authorization" not in request.headers
        assert request.extensions["timeout"]["connect"] == 10.0
        assert request.extensions["timeout"]["read"] == 15.0
        if request.method != "POST":
            return httpx.Response(405, request=request)
        body = json.loads(request.content)
        state.requests.append(body)
        method = body["method"]
        if method.startswith("notifications/"):
            return httpx.Response(202, request=request)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "parallel-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "web_search",
                        "description": "Search the public web for sources.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "objective": {"type": "string"},
                                "search_queries": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["objective", "search_queries"],
                        },
                    },
                    {
                        "name": "web_fetch",
                        "description": "Fetch source pages as markdown.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
                            "required": ["urls"],
                        },
                    },
                ]
            }
        elif method == "tools/call":
            if state.failure == "timeout":
                raise httpx.ReadTimeout("scripted read timeout", request=request)
            if state.failure == "cancel":
                await asyncio.Event().wait()
            result = {
                "content": [{"type": "text", "text": f"Python release notes: {SOURCE}"}],
                "isError": state.failure == "tool",
            }
            if state.failure == "http":
                return httpx.Response(503, request=request)
        else:
            raise AssertionError(method)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}, request=request
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return state


def enable(monkeypatch):
    monkeypatch.setenv("MCP_PARALLEL_SEARCH_ENABLED", "1")
    # Simulate the documented restart after changing an import-time setting.
    client._CACHED = None
    return {tool.name: tool for tool in client.load_external_tools()}


def call_args(name):
    if name == SEARCH:
        return {
            "objective": "Find current Python release notes",
            "search_queries": ["Python latest release notes"],
        }
    return {"urls": [SOURCE]}


@pytest.mark.parametrize("flag", [None, "0", "false", ""])
def test_default_and_disabled_do_not_connect(monkeypatch, mcp_http, flag):
    if flag is not None:
        monkeypatch.setenv("MCP_PARALLEL_SEARCH_ENABLED", flag)
    assert client.load_external_tools() == []
    assert not mcp_http.requests


def test_saved_servers_and_explicit_override_win(monkeypatch, mcp_http, tmp_path):
    monkeypatch.setenv("MCP_PARALLEL_SEARCH_ENABLED", "1")
    path = tmp_path / "mcp.json"
    custom = {"url": "https://example.org/custom", "headers": {"Authorization": "Bearer saved"}}
    path.write_text(
        json.dumps(
            {"mcpServers": {"mcp__parallel": custom, "filesystem": {"command": "local-mcp"}}}
        )
    )
    monkeypatch.setenv("MCP_CLIENT_CONFIG", str(path))
    cfg = client._normalize(client._load_config())
    assert cfg["mcp__parallel"] == {**custom, "transport": "streamable_http"}
    assert cfg["filesystem"]["command"] == "local-mcp"
    path.write_text(json.dumps({"mcpServers": {"mcp__parallel": {"disabled": True}}}))
    assert client.load_external_tools() == []
    assert not mcp_http.requests


@pytest.mark.parametrize("contents", [None, "{broken", "[]", '{"mcpServers": []}'])
def test_broken_explicit_config_does_not_enable_preset(monkeypatch, mcp_http, tmp_path, contents):
    monkeypatch.setenv("MCP_PARALLEL_SEARCH_ENABLED", "1")
    path = tmp_path / "mcp.json"
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setenv("MCP_CLIENT_CONFIG", str(path))
    assert client.load_external_tools() == []
    assert not mcp_http.requests


@pytest.mark.parametrize("name", [SEARCH, FETCH])
def test_discovery_dispatch_urls_and_consumable_output(monkeypatch, mcp_http, name):
    tools = enable(monkeypatch)
    assert set(tools) == {SEARCH, FETCH}
    assert client.load_external_tools() == list(tools.values())
    permissions.add_rule("mcp__parallel_*", "allow")
    result = safe_exec.safe_run_tool(tools[name], call_args(name), cacheable=False)
    assert SOURCE in result
    call = next(x["params"] for x in mcp_http.requests if x["method"] == "tools/call")
    assert call == {
        "name": "web_search" if name == SEARCH else "web_fetch",
        "arguments": call_args(name),
    }


@pytest.mark.parametrize("decision", ["ask", "deny"])
def test_remote_permission_gate_precedes_execution(monkeypatch, mcp_http, decision):
    tool = enable(monkeypatch)[SEARCH]
    if decision == "deny":
        permissions.add_rule("mcp__*", "deny")
        assert "[denied]" in safe_exec.safe_run_tool(tool, call_args(SEARCH))
    else:
        with pytest.raises(permissions.PermissionRequired):
            safe_exec.safe_run_tool(tool, call_args(SEARCH))
    assert not any(x["method"] == "tools/call" for x in mcp_http.requests)


@pytest.mark.parametrize("failure", ["tool", "http", "timeout"])
def test_failures_surface_without_provider_fallback(monkeypatch, mcp_http, failure):
    tools = enable(monkeypatch)
    permissions.add_rule("mcp__*", "allow")
    mcp_http.failure = failure
    result = safe_exec.safe_run_tool(tools[SEARCH], call_args(SEARCH), cacheable=False)
    assert "[error]" in result


@pytest.mark.asyncio
async def test_adapter_cancellation_closes_session(monkeypatch, mcp_http):
    # Loading from an active event loop also exercises the loader's thread path.
    tools = enable(monkeypatch)
    mcp_http.failure = "cancel"
    task = asyncio.create_task(tools[SEARCH].ainvoke(call_args(SEARCH)))
    for _ in range(100):
        if any(x["method"] == "tools/call" for x in mcp_http.requests):
            break
        await asyncio.sleep(0)
    assert any(x["method"] == "tools/call" for x in mcp_http.requests)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
async def test_harness_consumes_search_and_fetch_results(monkeypatch, mcp_http, tmp_path):
    from agent.harness import loop
    from agent.harness import tools as harness_tools
    from agent.harness.persistence import HarnessSessionStore
    from agent.tools import registry

    enable(monkeypatch)
    # Re-run the actual registry construction against this isolated loader cache.
    importlib.reload(registry)
    importlib.reload(harness_tools)
    try:
        assert {SEARCH, FETCH} <= {t.name for t in registry.all_tools}
        assert {SEARCH, FETCH} <= {t.name for t in harness_tools.HARNESS_TOOLS}
        harness = loop.Harness()
        harness.tools = harness_tools.HARNESS_TOOLS
        harness.tools_by_name = {t.name: t for t in harness.tools}
        harness.store = HarnessSessionStore(tmp_path / "sessions")
        permissions.add_rule("mcp__*", "allow")
        monkeypatch.setattr(harness, "_profile_blob", lambda: None)
        monkeypatch.setattr(
            loop, "get_llm", lambda **kwargs: SimpleNamespace(bind_tools=lambda tools: object())
        )
        seen = []

        async def scripted_model(llm, messages, session_id, step):
            seen.extend(m for m in messages if isinstance(m, ToolMessage))
            if step < 2:
                name = SEARCH if step == 0 else FETCH
                if step == 1:
                    assert SOURCE in seen[-1].content
                return AIMessage(
                    content="",
                    tool_calls=[{"name": name, "args": call_args(name), "id": str(step)}],
                )
            assert len({m.tool_call_id for m in seen}) == 2
            assert all(SOURCE in m.content for m in seen)
            return AIMessage(content=f"Python release notes: {SOURCE}")

        monkeypatch.setattr(harness, "_stream_llm", scripted_model)
        events = [
            e
            async for e in harness.run(
                "Find Python release notes and read the source", "parallel-fixture"
            )
        ]
        assert SOURCE in events[-1]["payload"]["text"]
    finally:
        monkeypatch.delenv("MCP_PARALLEL_SEARCH_ENABLED")
        client._CACHED = None
        importlib.reload(registry)
        importlib.reload(harness_tools)


@pytest.mark.parametrize("flag", [None, "1"])
@pytest.mark.parametrize("tavily", [False, True])
def test_builtin_search_selection_is_preserved(monkeypatch, mcp_http, flag, tavily):
    search = importlib.import_module("agent.tools.web_search")
    if flag:
        monkeypatch.setenv("MCP_PARALLEL_SEARCH_ENABLED", flag)
    if tavily:
        monkeypatch.setenv("TAVILY_API_KEY", "saved-key")
    calls = []

    def ddg(query, max_results):
        calls.append("ddg")
        return [{"title": "DDG source", "url": SOURCE, "content": "release"}]

    def configured(query, max_results):
        calls.append("tavily")
        return [{"title": "Tavily source", "url": SOURCE, "content": "release"}]

    monkeypatch.setattr(search, "_search_ddg", ddg)
    monkeypatch.setattr(search, "_search_tavily", configured)
    result = safe_exec.safe_run_tool(
        search.web_search, {"query": "Python releases"}, cacheable=False
    )
    assert SOURCE in result
    assert calls == ["tavily" if tavily else "ddg"]
    assert not mcp_http.requests


def test_graph_dispatch_consumes_mcp_result(monkeypatch, mcp_http):
    executor = importlib.import_module("agent.nodes.executor")
    enable(monkeypatch)
    from agent.tools import registry

    permissions.add_rule("mcp__*", "allow")
    importlib.reload(registry)
    monkeypatch.setattr(executor, "_TOOLS_BY_NAME", {t.name: t for t in registry.all_tools})
    try:
        result = executor.tool_node(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": SEARCH, "args": call_args(SEARCH), "id": "graph-search"}
                        ],
                    )
                ]
            }
        )
        message = result["messages"][0]
        assert isinstance(message, ToolMessage)
        assert message.tool_call_id == "graph-search"
        assert SOURCE in message.content
    finally:
        monkeypatch.delenv("MCP_PARALLEL_SEARCH_ENABLED")
        client._CACHED = None
        importlib.reload(registry)


def test_alternate_saved_config_shape_preserves_other_servers(monkeypatch, mcp_http, tmp_path):
    monkeypatch.setenv("MCP_PARALLEL_SEARCH_ENABLED", "1")
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {}, "servers": {"custom": {"command": "local-mcp"}}}))
    monkeypatch.setenv("MCP_CLIENT_CONFIG", str(path))
    cfg = client._normalize(client._load_config())
    assert set(cfg) == {"custom", "mcp__parallel"}
    assert cfg["custom"]["command"] == "local-mcp"
    assert cfg["mcp__parallel"]["url"] == URL
