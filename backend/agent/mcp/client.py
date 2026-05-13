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

If `MCP_CLIENT_CONFIG` is unset, this module is a no-op and our registry
keeps its original built-in tools only.

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
from typing import Any

from langchain_core.tools import BaseTool

log = logging.getLogger("agent.mcp.client")


def _load_config() -> dict[str, Any] | None:
    raw = (os.environ.get("MCP_CLIENT_CONFIG") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.exists():
        log.warning("MCP_CLIENT_CONFIG=%s does not exist · skipping", p)
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("MCP_CLIENT_CONFIG=%s failed to parse · %r", p, e)
        return None

    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict) or not servers:
        return None
    return servers


def _normalize(servers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Coerce Claude-Desktop-shaped entries into adapter-shaped ones."""
    out: dict[str, dict[str, Any]] = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if "url" in cfg:
            transport = cfg.get("transport", "streamable_http")
            entry: dict[str, Any] = {"url": cfg["url"], "transport": transport}
            if "headers" in cfg:
                entry["headers"] = cfg["headers"]
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
    except Exception as e:  # noqa: BLE001
        log.warning("langchain-mcp-adapters not installed · %r", e)
        _CACHED = []
        return _CACHED

    async def _fetch() -> list[BaseTool]:
        # tool_name_prefix=True names tools as `<server>__<tool>` to avoid
        # collisions with our built-in tools (e.g. our `read_file` vs
        # filesystem-server's `read_file`).
        client = MultiServerMCPClient(connections, tool_name_prefix=True)
        tools = await client.get_tools()
        return list(tools)

    try:
        tools = asyncio.run(_fetch())
    except RuntimeError:
        # If we're already inside an event loop (e.g. test harness),
        # create a fresh thread with its own loop.
        import threading

        result: dict[str, Any] = {}

        def _runner() -> None:
            try:
                result["tools"] = asyncio.run(_fetch())
            except Exception as e:  # noqa: BLE001
                result["error"] = e

        t = threading.Thread(target=_runner)
        t.start()
        t.join(timeout=30)
        if "error" in result:
            log.warning("MCP client fetch failed · %r", result["error"])
            _CACHED = []
            return _CACHED
        tools = result.get("tools", [])
    except Exception as e:  # noqa: BLE001
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
