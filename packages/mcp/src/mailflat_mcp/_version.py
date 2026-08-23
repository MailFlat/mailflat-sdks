"""The package's single version constant.

Why its own module: `__init__` imports from `.server`, and `server` needs the version for the
MCP `serverInfo` handshake — reading it from `__init__` would be a circular import. A leaf
module breaks the cycle. (The JS SDK has the same file for the same reason.)

pyproject reads this path via [tool.hatch.version], so releasing means bumping this one line.

Connected to:
  - used by: mailflat_mcp.__init__ (re-export), mailflat_mcp.server (serverInfo)

Key export: __version__
"""

__version__ = "0.9.1"
