"""A tiny stdio MCP server for the connector tests: `python tests/fixtures/mcp_server.py`."""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("alfred-test")


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool()
def whoami() -> str:
    """The token this server was started with (from its environment)."""
    return os.environ.get("TEST_MCP_TOKEN", "")


@server.tool()
def wipe_disk() -> str:
    """Not declared by the connector, so it must never reach an agent."""
    return "wiped"


if __name__ == "__main__":
    server.run()
