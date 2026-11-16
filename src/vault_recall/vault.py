"""Reading a vault: note discovery, frontmatter, links and link resolution."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import yaml

from .config import Config

MAX_NOTE_BYTES = 1_000_000
EMBED_CHARS = 3000

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
_MDLINK = re.compile(r"(?<!!)\[[^\[\]\n]*\]\(\s*(<[^>\n]+>|[^()\s]+)(?:\s+\"[^\"\n]*\")?\s*\)")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class Note:
    path: Path  # absolute
    rel: str  # POSIX path relative to the vault
    stem: str
    meta: dict
    frontmatter: str  # "ok", "none" or "broken"
    body: str
    body_line: int  # 1-based line of the file on which ``body`` starts
    sha256: str
    mtime: float
    is_index: bool = False

    @property
    def name(self) -> str | None:
        value = self.meta.get("name")
        return str(value).strip() if value not in (None, "") else None

    @property
    def description(self) -> str:
        value = self.meta.get("description")
        return " ".join(str(value).split()) if value not in (None, "") else ""

    @property
    def title(self) -> str:
        return self.stem.replace("-", " ").replace("_", " ")

    @property
    def label(self) -> str:
        return self.name or self.stem

    def rank_text(self) -> str:
        """Text for BM25: the file name counts twice as a cheap title boost."""
        return "\n".join([self.title, self.title, self.name or "", self.description, self.body])

    def embed_text(self) -> str:
        text = "\n".join([self.title, self.name or "", self.description, self.body])
        return text[:EMBED_CHARS]


def parse_frontmatter(text: str) -> tuple[dict, str, str, int]:
    """Split a note into ``(meta, body, status, body_line)``.

    Handles a BOM and CRLF line endings. A block that is not valid YAML, or not a mapping,
    is reported as ``broken`` and the body still starts after it.
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}, text, "none", 1
    for end in range(1, len(lines)):
        if lines[end].rstrip() in ("---", "..."):
            raw = "\n".join(lines[1:end])
            body = "\n".join(lines[end + 1 :])
            try:
                meta = yaml.safe_load(raw) if raw.strip() else {}
            except yaml.YAMLError:
                return {}, body, "broken", end + 2
            if not isinstance(meta, dict):
                return {}, body, "broken", end + 2
            return {str(k): v for k, v in meta.items()}, body, "ok", end + 2
    return {}, text, "none", 1


def parse_note(path: Path, root: Path, is_index: bool = False) -> Note | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_NOTE_BYTES:
        return None
    text = raw.decode("utf-8", errors="replace")
    meta, body, status, body_line = parse_frontmatter(text)
    return Note(
        path=path,
        rel=path.relative_to(root).as_posix(),
        stem=path.stem,
        meta=meta,
        frontmatter=status,
        body=body,
        body_line=body_line,
        sha256=hashlib.sha256(raw).hexdigest(),
        mtime=path.stat().st_mtime,
        is_index=is_index,
    )


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def is_excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(_glob_regex(p).match(rel) for p in patterns)


@dataclass(frozen=True)
class Vault:
    root: Path
    notes: tuple[Note, ...]  # searchable notes, index files excluded
    indexes: tuple[Note, ...]
    files: frozenset[str]  # every non-Markdown file name, for ``[[image.png]]`` links


def load_vault(cfg: Config) -> Vault:
    """Read every Markdown file that is not excluded. Never follows links out of the vault."""
    root = cfg.vault
    index_rels = {Path(p).as_posix() for p in cfg.index_files}
    notes: list[Note] = []
    indexes: list[Note] = []
    others: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        rel_dir = base.relative_to(root).as_posix()
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not is_excluded(f"{d}/x" if rel_dir == "." else f"{rel_dir}/{d}/x", cfg.exclude)
        )
        for filename in sorted(filenames):
            path = base / filename
            rel = filename if rel_dir == "." else f"{rel_dir}/{filename}"
            if is_excluded(rel, cfg.exclude):
                continue
            if not filename.lower().endswith(".md"):
                others.add(filename.lower())
                continue
            if path.is_symlink() and not _inside(path.resolve(), root):
                continue
            is_index = rel in index_rels
            note = parse_note(path, root, is_index)
            if note is not None:
                (indexes if is_index else notes).append(note)
    return Vault(root, tuple(notes), tuple(indexes), frozenset(others))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------- links


def mask_fenced(text: str) -> str:
    """Blank out fenced code blocks, keeping the line count."""
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                out.append("")
            else:
                out.append(line)
        else:
            out.append("")
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
    return "\n".join(out)


def mask_code(text: str) -> str:
    """Blank out fenced blocks and inline code spans, keeping offsets and line count."""
    return _INLINE_CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), mask_fenced(text))


@dataclass(frozen=True)
class Link:
    kind: str  # "wiki" or "md"
    target: str
    line: int  # 1-based line in the file


def extract_links(note: Note) -> list[Link]:
    """Wikilinks and Markdown links in the body, ignoring anything inside code."""
    masked = mask_code(note.body)
    links: list[Link] = []
    for lineno, line in enumerate(masked.split("\n"), start=note.body_line):
        for m in _WIKILINK.finditer(line):
            target = m.group(1).split("|", 1)[0].split("#", 1)[0].split("^", 1)[0].strip()
            if target:
                links.append(Link("wiki", target, lineno))
        for m in _MDLINK.finditer(line):
            target = m.group(1).strip("<>")
            if target.startswith("#") or _SCHEME.match(target):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if target:
                links.append(Link("md", target, lineno))
    return links


_PATH_SPAN = re.compile(r"^(?:~/|/)[^\s*?<>{}\[\]\u2026$|;&\"']+$")


_PLACEHOLDER = re.compile(r"(?<![A-Za-z])(?:YYYY|MM|DD|X{3,})(?![A-Za-z])")


def _is_filesystem_path(path: str) -> bool:
    """Home paths always count. An absolute path counts only if its first directory exists
    here (``/Users``, ``/etc``); anything else is far more likely a URL route.
    """
    if "..." in path or _PLACEHOLDER.search(path):
        return False
    if path.startswith("~/"):
        return True
    first = path.split("/")[1]
    if not first or (path.count("/") == 2 and path.endswith("/")):
        return False
    return os.path.isdir("/" + first)


def backticked_paths(note: Note) -> list[tuple[int, str]]:
    """Inline code spans that look like an absolute or home-relative path."""
    found: list[tuple[int, str]] = []
    masked = mask_fenced(note.body)
    for lineno, line in enumerate(masked.split("\n"), start=note.body_line):
        for m in _INLINE_CODE.finditer(line):
            span = m.group(2).strip()
            if not _PATH_SPAN.match(span):
                continue
            path = span.rstrip(".,:;)")
            if _is_filesystem_path(path):
                found.append((lineno, path))
    return found


class Resolver:
    """Resolves link targets the way Obsidian does: by file stem or frontmatter name."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._by_stem: dict[str, list[Note]] = {}
        self._by_name: dict[str, list[Note]] = {}
        self._by_path: dict[Path, Note] = {}
        for note in (*vault.notes, *vault.indexes):
            self._by_path[note.path.resolve()] = note
            self._by_stem.setdefault(note.stem.lower(), []).append(note)
            if note.name:
                self._by_name.setdefault(note.name.lower(), []).append(note)

    def lookup(self, name: str) -> Note | None:
        """A note by file stem, then by frontmatter name; case-insensitive, first match wins."""
        key = name.strip().lower()
        matches = self._by_stem.get(key) or self._by_name.get(key)
        return matches[0] if matches else None

    def resolve(self, link: Link, source: Note) -> Note | bool | None:
        """The linked note, ``True`` for an existing non-note file, ``None`` if broken."""
        if link.kind == "md":
            target = (source.path.parent / link.target).resolve()
            if target in self._by_path:
                return self._by_path[target]
            return True if target.is_file() and not link.target.lower().endswith(".md") else None
        target = link.target.replace("\\", "/")
        leaf = target.rsplit("/", 1)[-1]
        if leaf.lower().endswith(".md"):
            leaf = leaf[:-3]
        found = self.lookup(leaf)
        if found is not None:
            return found
        if "." in leaf and leaf.lower() in self.vault.files:
            return True
        return None

    def inbound(self) -> dict[Path, list[tuple[Note, Link]]]:
        """Resolved links grouped by target note path."""
        graph: dict[Path, list[tuple[Note, Link]]] = {}
        for source in (*self.vault.notes, *self.vault.indexes):
            for link in extract_links(source):
                target = self.resolve(link, source)
                if isinstance(target, Note) and target.path != source.path:
                    graph.setdefault(target.path, []).append((source, link))
        return graph
