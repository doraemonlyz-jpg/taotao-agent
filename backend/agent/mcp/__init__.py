"""
MCP (Model Context Protocol) integration · two halves.

  - server.py · expose taotao-agent's tools/prompts/resources as an MCP
    server. Other clients (Claude Desktop, Cursor, Cody, ...) can mount
    it and call our tools directly.

  - client.py · let taotao-agent consume *other* MCP servers (filesystem,
    github, fetch, ...). Their tools auto-merge into both registries.

Both layers are env-gated · disabled by default so a vanilla install
keeps the same surface.
"""
