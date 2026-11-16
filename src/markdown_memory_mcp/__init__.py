"""Hybrid recall, lint and MCP server for a folder of Markdown notes."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("markdown-memory-mcp")
except PackageNotFoundError:  # running from a checkout that was never installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
