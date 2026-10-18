"""
MCP client · pull tools from *other* MCP servers into our registry.

Config
------
Set `MCP_CLIENT_CONFIG=path/to/mcp_servers.json` to enable. The file is
the same shape as Claude Desktop's `claude_desktop_config.json`:

    {
      "mcpServers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        },
        "remote-thing": {
          "url": "http://example.com/mcp/",
          "transport": "streamable_http"
        }
      }
    }

Two transports supported:

  - **stdio**            commands the SDK will spawn as a subprocess.
                         Requires the binary on $PATH (npx / uvx / etc.).

  - **streamable_http**  hits a remote MCP server's /mcp/ endpoint.
                         Add `headers: {Authorization: Bearer ...}` if
                         the server requires auth.

Set `MCP_PARALLEL_SEARCH_ENABLED=1` to add the built-in anonymous Parallel
Search MCP preset. It works independently of `MCP_CLIENT_CONFIG`; entries
in that file override the preset. With neither setting, this module is
a no-op and our registry keeps its original built-in tools only.

Lifetime
--------
We resolve external tools **once at import** by spinning up a temporary
asyncio loop, fetching the tool objects, and caching them. Each tool's
`.invoke()` opens its own short-lived MCP session — that's how
`langchain-mcp-adapters` is designed to work outside of LangGraph
streaming. The trade-off: simple, but no connection pooling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, cast

from langchain_core.tools import BaseTool

log = logging.getLogger("agent.mcp.client")


def _builtin_servers() -> dict[str, Any]:
    """Return explicitly enabled presets, without reading API keys."""
    if os.environ.get("MCP_PARALLEL_SEARCH_ENABLED", "").strip() != "1":
        return {}
    return {
        # Keep the least-trust mcp__* permission rule for remote tools.
        "mcp__parallel": {
            "url": "https://search.parallel.ai/mcp",
            "transport": "streamable_http",
            "headers": {"User-Agent": "taotao-agent/0.1.0"},
            "timeout": 10.0,
            "sse_read_timeout": 15.0,
        }
    }


def _load_config() -> dict[str, Any] | None:
    presets = _builtin_servers()
    raw = (os.environ.get("MCP_CLIENT_CONFIG") or "").strip()
    if not raw:
        return presets or None
    p = Path(raw).expanduser()
    if not p.exists():
        log.warning("MCP_CLIENT_CONFIG=%s does not exist · skipping", p)
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("MCP_CLIENT_CONFIG=%s failed to parse · %r", p, e)
        return None
    if not isinstance(data, dict):
        log.warning("MCP_CLIENT_CONFIG=%s must contain a JSON object · skipping", p)
        return None

    servers = data.get("mcpServers")
    if servers is None or servers == {}:
        servers = data.get("servers", {})
    if not isinstance(servers, dict):
        return None
    # A saved entry wins in full, including {"disabled": true}. Never
    # replace an explicit server URL, credentials or headers with a preset.
    return {**presets, **servers} or None


def _normalize(servers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Coerce Claude-Desktop-shaped entries into adapter-shaped ones."""
    out: dict[str, dict[str, Any]] = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict) or cfg.get("disabled") is True:
            continue
        if "url" in cfg:
            transport = cfg.get("transport", "streamable_http")
            entry: dict[str, Any] = {"url": cfg["url"], "transport": transport}
            if "headers" in cfg:
                entry["headers"] = cfg["headers"]
            for option in ("timeout", "sse_read_timeout"):
                if option in cfg:
                    entry[option] = cfg[option]
            out[name] = entry
        elif "command" in cfg:
            entry = {
                "command": cfg["command"],
                "args": cfg.get("args", []),
                "transport": "stdio",
            }
            if "env" in cfg:
                entry["env"] = cfg["env"]
            out[name] = entry
        else:
            log.warning("MCP server '%s' has neither url nor command · skipping", name)
    return out


_CACHED: list[BaseTool] | None = None
_STATUS: dict[str, Any] = {"enabled": False, "servers": {}, "tool_count": 0}


def get_status() -> dict[str, Any]:
    return dict(_STATUS)


def load_external_tools() -> list[BaseTool]:
    """Return cached list of LangChain tools fetched from external MCP servers.

    First call does the actual fetch (one shot, blocking with its own loop);
    later calls are free. Errors degrade to an empty list so a busted
    config never breaks the app.
    """
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    cfg = _load_config()
    if not cfg:
        _CACHED = []
        return _CACHED

    connections = _normalize(cfg)
    if not connections:
        _CACHED = []
        return _CACHED

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.sessions import Connection
    except Exception as e:
        log.warning("langchain-mcp-adapters not installed · %r", e)
        _CACHED = []
        return _CACHED

    async def _fetch() -> list[BaseTool]:
        # tool_name_prefix=True names tools as `<server>_<tool>` to avoid
        # collisions with our built-in tools (e.g. our `read_file` vs
        # filesystem-server's `read_file`).
        client = MultiServerMCPClient(
            cast("dict[str, Connection]", connections), tool_name_prefix=True
        )
        tools = await client.get_tools()
        return list(tools)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        in_event_loop = False
    else:
        in_event_loop = True

    def _resolve() -> list[BaseTool]:
        if not in_event_loop:
            return asyncio.run(_fetch())
        # Import-time loading may run inside the application's event loop.
        # Detect that before creating a coroutine, so none is left unawaited.
        import threading

        result: dict[str, Any] = {}

        def _runner() -> None:
            try:
                result["tools"] = asyncio.run(_fetch())
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=_runner)
        t.start()
        t.join(timeout=30)
        if "error" in result:
            raise result["error"]
        return result.get("tools", [])

    try:
        tools = _resolve()
    except Exception as e:
        log.warning("MCP client fetch failed · %r", e)
        _CACHED = []
        return _CACHED

    _CACHED = tools
    _STATUS.update(
        enabled=True,
        servers=list(connections.keys()),
        tool_count=len(tools),
        tool_names=[t.name for t in tools],
    )
    log.info("MCP client loaded %d tool(s) from %d server(s)", len(tools), len(connections))
    return _CACHED
