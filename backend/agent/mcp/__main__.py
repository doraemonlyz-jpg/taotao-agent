"""`python -m agent.mcp` → stdio MCP server (for Claude Desktop / Cursor)."""
from .server import run_stdio

if __name__ == "__main__":
    run_stdio()
