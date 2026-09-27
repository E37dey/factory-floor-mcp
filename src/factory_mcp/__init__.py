"""factory-floor-mcp: a read-only MCP server for manufacturing plant data."""

from .server import create_server

__all__ = ["create_server"]
__version__ = "0.1.0"
