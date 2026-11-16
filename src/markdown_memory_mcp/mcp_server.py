"""MCP server over stdio. The tool logic is plain Python so it can be tested directly."""

from __future__ import annotations

import os
import re
import stat
import uuid
from pathlib import Path

import yaml

from .config import Config
from .embed import INSTALL_HINT, EmbedderFactory
from .lint import lint, stored_vectors
from .search import build_corpus
from .vault import MAX_NOTE_BYTES, Resolver, load_vault

SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,79}[a-z0-9])?$")
TYPE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_UPDATES = 200


class ToolError(ValueError):
    """A request the server refuses. The message is shown to the client."""


class VaultTools:
    def __init__(self, cfg: Config, factory: EmbedderFactory | None, allow_write: bool) -> None:
        self.cfg = cfg
        self.root = cfg.vault.resolve()
        self.factory = factory
        self.allow_write = allow_write

    def search_notes(self, query: str, k: int = 5) -> list[dict]:
        """Find notes by meaning and by words. Use this to recall what the vault knows.

        Args:
            query: What to look for, in natural language.
            k: How many notes to return (1-50).

        Returns a list, best first, of {name, path, description, rrf, cosine, bm25}. ``path`` is
        relative to the vault; pass ``name`` to read_note for the full text. ``cosine`` is null
        when the note has no embedding yet.
        """
        if not query.strip():
            raise ToolError("query is empty")
        corpus = build_corpus(self.cfg, self.factory, max_updates=MAX_UPDATES)
        if corpus is None:
            raise ToolError("no index available")
        return [h.to_dict() for h in corpus.search(query, k=max(1, min(k, 50)))]

    def read_note(self, name: str) -> dict:
        """Read one note in full.

        Args:
            name: The note's file name without ``.md``, or its frontmatter ``name``. Paths are
                refused.

        Returns {name, path, description, content}, where content is the whole file.
        """
        note = self._find(name)
        resolved = note.path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolError("note is outside the vault")
        return {
            "name": note.label,
            "path": note.rel,
            "description": note.description,
            "content": resolved.read_text(encoding="utf-8", errors="replace"),
        }

    def backlinks(self, name: str) -> list[dict]:
        """List the notes and index files that link to a note, by [[wikilink]] or Markdown link.

        Args:
            name: The note's file name without ``.md``, or its frontmatter ``name``.

        Returns a list of {name, path, line}. Empty if nothing links to it.
        """
        target = self._find(name)
        sources = Resolver(load_vault(self.cfg)).inbound().get(target.path, [])
        return [
            {"name": src.label, "path": src.rel, "line": link.line}
            for src, link in sorted(sources, key=lambda s: (s[0].rel, s[1].line))
        ]

    def lint_vault(self) -> dict:
        """Check the vault for broken links, missing frontmatter, duplicate names, orphans and an
        oversized index. Takes no arguments.

        Returns {findings: [{code, level, path, line, message}]}. Codes VR001-VR010.
        """
        vault = load_vault(self.cfg)
        findings = lint(self.cfg, vault, stored_vectors(self.cfg, vault))
        return {"findings": [f.to_dict() for f in findings]}

    def write_note(
        self,
        name: str,
        description: str,
        body: str,
        type: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Create a note in the vault root. Use this to save something worth remembering.

        Args:
            name: File name without ``.md``: lowercase letters, digits and dashes, 1-81
                characters, starting and ending with a letter or digit.
            description: One line saying what the note is for (1-500 characters).
            body: Markdown body of the note.
            type: Optional frontmatter ``type``, for example ``reference`` or ``feedback``.
            overwrite: Replace an existing note of that name. Default false: refuse instead.

        Index files are never written. Returns {path}.
        """
        if not self.allow_write:
            raise ToolError("writing is disabled; start the server with --allow-write")
        target = self._write_target(name)
        description = " ".join(description.split())
        if not description or len(description) > 500:
            raise ToolError("description must be 1-500 characters")
        if type is not None and not TYPE.match(type):
            raise ToolError("type must match [A-Za-z0-9_-]{1,40}")
        if len(body.encode()) > MAX_NOTE_BYTES // 2:
            raise ToolError("body is too large")
        if target.exists() and not overwrite:
            raise ToolError(f"{target.name} already exists; pass overwrite=true to replace it")
        vault = load_vault(self.cfg)
        clash = Resolver(vault).lookup(name)
        if clash is not None and clash.path.resolve() != target.resolve() and not overwrite:
            raise ToolError(f"a note named '{name}' already exists at {clash.rel}")

        meta: dict[str, str] = {"name": name, "description": description}
        if type:
            meta["type"] = type
        front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10_000)
        text = f"---\n{front}---\n\n{body.strip()}\n"

        # mkstemp would force mode 0600. Creating the file with 0666 lets the umask decide,
        # as for any file the user creates.
        tmp = str(self.root / f".write-note-{uuid.uuid4().hex}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            if overwrite:
                if target.exists():
                    os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
                os.replace(tmp, target)
            else:
                # link() fails if the target appeared since the check above.
                try:
                    os.link(tmp, target)
                except FileExistsError:
                    raise ToolError(f"{target.name} already exists") from None
        finally:
            Path(tmp).unlink(missing_ok=True)
        return {"path": target.name}

    # -- helpers

    def _find(self, name: str):
        self._reject_path_like(name)
        note = Resolver(load_vault(self.cfg)).lookup(name.removesuffix(".md"))
        if note is None:
            raise ToolError(f"no note named '{name}'")
        return note

    @staticmethod
    def _reject_path_like(name: str) -> None:
        if not name.strip() or "/" in name or "\\" in name or ".." in name or "\0" in name:
            raise ToolError("name must be a note name, not a path")
        if os.path.isabs(name) or name.startswith("~"):
            raise ToolError("name must be a note name, not a path")

    def _is_index_file(self, filename: str, target: Path) -> bool:
        """True if ``target`` is, or on a case-insensitive file system would be, an index file."""
        wanted = os.path.normcase(filename).casefold()
        for index in self.cfg.index_files:
            candidate = self.root / index
            if os.path.normcase(Path(index).as_posix()).casefold() == wanted:
                return True
            if target.exists() and candidate.exists() and os.path.samefile(target, candidate):
                return True
        existing = Resolver(load_vault(self.cfg)).lookup(filename.removesuffix(".md"))
        return existing is not None and existing.is_index

    def _write_target(self, name: str) -> Path:
        self._reject_path_like(name)
        if not SLUG.match(name):
            raise ToolError(
                "name must be 1-81 lowercase letters, digits or dashes, not ending in a dash"
            )
        filename = f"{name}.md"
        target = self.root / filename
        if self._is_index_file(filename, target):
            raise ToolError("index files are never edited by this tool")
        if target.is_symlink():
            raise ToolError("refusing to write through a symlink")
        if not target.resolve().is_relative_to(self.root) or target.resolve().parent != self.root:
            raise ToolError("path escapes the vault")
        return target


def build_server(tools: VaultTools):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(f"the MCP server needs the mcp extra; {INSTALL_HINT}") from exc

    server = FastMCP("markdown-memory-mcp")
    server.tool()(tools.search_notes)
    server.tool()(tools.read_note)
    server.tool()(tools.backlinks)
    server.tool()(tools.lint_vault)
    if tools.allow_write:
        server.tool()(tools.write_note)
    return server
