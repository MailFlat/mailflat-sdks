"""MailFlat MCP server package — an MCP tool set built on the `mailflat` SDK.

Runs via `uvx mailflat-mcp`; exposes inbox tools to Claude Desktop, Cursor and any agent
framework that speaks MCP.

Connected to:
  - imports from: mailflat_mcp.server
  - imported by:  the `mailflat-mcp` console script, MCP clients

Key exports:
  - `mcp` — FastMCP instance
  - `main()` — stdio server entry point
"""
from .server import main, mcp

from ._version import __version__

__all__ = ["mcp", "main", "__version__"]
