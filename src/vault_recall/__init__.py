"""Hybrid recall, lint and MCP server for a folder of Markdown notes."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vault-recall")
except PackageNotFoundError:  # running from a checkout that was never installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
