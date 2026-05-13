"""
MCP server · re-exports our @tool functions over the Model Context Protocol.

Two transports, same registry:

  - **stdio**            for Claude Desktop / Cursor / generic MCP clients
                         that spawn the server as a subprocess.
                         Run: `uv run python -m agent.mcp`

  - **streamable_http**  mounted onto the FastAPI app at /mcp by app.py.
                         Useful when you want remote / browser MCP clients.

Whitelist
---------
Tools are exposed only if their `name` is in `MCP_EXPOSED_TOOLS`
(comma-separated env var). Default: read-only safe tools only.

Why a whitelist? MCP clients are arbitrary processes — exposing
`write_file` or `python_repl` to anything that happens to attach is a
foot-gun. Opt-in is the safe default. To open everything up:

    MCP_EXPOSED_TOOLS=*

Auth
----
If `MCP_AUTH_TOKEN` is set, the streamable_http transport requires
clients to send `Authorization: Bearer <token>`. stdio transport runs
under your user's identity already, so no auth is enforced there.
"""
from __future__ import annotations

import inspect
import os
from typing import Any, Callable

from langchain_core.tools import BaseTool
from mcp.server.fastmcp import FastMCP

# All tools live in the graph-side registry; we don't expose harness-
# specific synthetic tools (dispatch_subagent, final_answer) over MCP
# because they only make sense inside the harness loop.
from ..tools.registry import all_tools

# ----------------------------------------------------------------- defaults
DEFAULT_SAFE_TOOLS = {
    "calculator",
    "current_time",
    "web_search",
    "list_files",
    "read_file",
    "grep_in_files",
    "recall",
    "read_profile",
    "load_skill",
}


def _exposed_names() -> set[str]:
    raw = (os.environ.get("MCP_EXPOSED_TOOLS") or "").strip()
    if not raw:
        return DEFAULT_SAFE_TOOLS
    if raw == "*":
        return {t.name for t in all_tools}
    return {n.strip() for n in raw.split(",") if n.strip()}


# ----------------------------------------------------------------- adapter
def _wrap_tool(t: BaseTool) -> Callable[..., Any]:
    """Convert a LangChain BaseTool into a callable that FastMCP can bind.

    FastMCP infers the JSON schema from the wrapper's signature, so we
    rebuild the parameter list from the tool's args_schema (Pydantic) and
    forward to `t.invoke(args_dict)`.
    """
    schema = t.args_schema
    fields = getattr(schema, "model_fields", {}) if schema else {}

    # Build a real Python signature so FastMCP picks correct param names,
    # types, and defaults.
    params: list[inspect.Parameter] = []
    for name, finfo in fields.items():
        anno = finfo.annotation if finfo.annotation is not None else Any
        default = (
            inspect.Parameter.empty
            if finfo.is_required()
            else finfo.default
        )
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=anno,
            )
        )
    sig = inspect.Signature(parameters=params, return_annotation=str)

    def runner(**kwargs: Any) -> str:
        # invoke() handles both sync and async tools transparently.
        result = t.invoke(kwargs)
        return result if isinstance(result, str) else str(result)

    runner.__name__ = t.name
    runner.__doc__ = t.description or t.name
    runner.__signature__ = sig  # type: ignore[attr-defined]
    return runner


# ----------------------------------------------------------------- builder
def build_mcp_server() -> FastMCP:
    """Construct (but don't run) a FastMCP server registered with all
    whitelisted tools, plus a couple of useful prompts/resources."""
    server = FastMCP(
        name="taotao-agent",
        instructions=(
            "Tools, prompts, and read-only resources from the taotao-agent "
            "project. Tools cover math, time, web search, file reading, "
            "grep, memory recall, profile reading, and skill loading. "
            "Write-side tools (write_file, remember, python_repl) are NOT "
            "exposed by default."
        ),
        # Make the internal route the mount root so that when app.py
        # mounts us at "/mcp", the JSON-RPC endpoint is exactly /mcp/
        # (instead of the doubled-up /mcp/mcp).
        streamable_http_path="/",
        # Allow Mcp-Session-Id management to skip transport-security
        # checks during local dev (curl from same host etc.).
        stateless_http=True,
        json_response=True,
    )

    expose = _exposed_names()
    skipped: list[str] = []
    registered: list[str] = []
    for t in all_tools:
        if t.name not in expose:
            skipped.append(t.name)
            continue
        server.add_tool(
            _wrap_tool(t),
            name=t.name,
            description=(t.description or "").strip(),
        )
        registered.append(t.name)

    # ------- prompt: ask the agent
    @server.prompt(name="ask_agent", description="Ask the taotao-agent a question (returns a prompt for the calling LLM to handle).")
    def ask_agent(question: str) -> str:
        return (
            f"Use the available taotao-agent tools to answer this question:\n\n{question}\n\n"
            "Pick the right tool, call it, then summarise the result for the user."
        )

    # ------- resource: server status
    @server.resource("taotao://status", name="status", description="Server identity + which tools are exposed.")
    def status() -> str:
        import json
        return json.dumps({
            "server": "taotao-agent",
            "version": "0.2.0",
            "exposed_tools": sorted(registered),
            "hidden_tools": sorted(skipped),
            "policy": "set MCP_EXPOSED_TOOLS=* to expose everything",
        }, indent=2)

    return server


# ----------------------------------------------------------------- stdio entry
def run_stdio() -> None:
    """Entry for `python -m agent.mcp` (Claude Desktop / Cursor subprocess)."""
    server = build_mcp_server()
    server.run("stdio")


if __name__ == "__main__":
    run_stdio()
