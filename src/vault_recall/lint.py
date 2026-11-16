"""Vault checks VR001-VR010. Read-only."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import Config
from .embed import embeddings_installed
from .store import Store
from .vault import Note, Resolver, Vault, backticked_paths, extract_links, is_excluded

if TYPE_CHECKING:
    import numpy as np

LEVELS = {"error": 2, "warning": 1, "info": 0}


@dataclass(frozen=True)
class Finding:
    code: str
    level: str
    path: str
    line: int | None
    message: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "level": self.level,
            "path": self.path,
            "line": self.line,
            "message": self.message,
        }


def stored_vectors(cfg: Config, vault: Vault) -> dict[str, np.ndarray] | None:
    """Vectors from an existing index, or None when VR008 cannot run."""
    if not embeddings_installed():
        return None
    store = Store(cfg.vault, cfg.model)
    if not store.exists():
        return None
    return store.vectors_for(vault.notes) or None


def lint(cfg: Config, vault: Vault, vectors: dict[str, np.ndarray] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    resolver = Resolver(vault)

    def add(code: str, level: str, note: Note, line: int | None, message: str) -> None:
        findings.append(Finding(code, level, note.rel, line, message))

    for note in (*vault.notes, *vault.indexes):
        for link in extract_links(note):
            target = resolver.resolve(link, note)
            if target is not None:
                continue
            if link.kind == "wiki":
                add("VR001", "error", note, link.line, f"broken wikilink [[{link.target}]]")
            elif note.is_index and link.target.lower().endswith(".md"):
                add("VR002", "error", note, link.line, f"index links to missing file {link.target}")
        for line, raw in backticked_paths(note):
            if not os.path.exists(os.path.expanduser(raw)):
                add("VR009", "warning", note, line, f"path in code span does not exist: {raw}")

    _check_frontmatter(cfg, vault, add)
    _check_orphans(vault, resolver, add)
    _check_indexes(cfg, vault, add)
    if vectors:
        _check_near_duplicates(cfg, vault, vectors, add)

    # Files matching lint_exclude are still read, so links from and to them keep resolving,
    # but nothing is reported about them.
    findings = [f for f in findings if not is_excluded(f.path, cfg.lint_exclude)]
    findings.sort(key=lambda f: (f.path, f.line or 0, f.code))
    return findings


def _check_frontmatter(cfg: Config, vault: Vault, add) -> None:
    by_name: dict[str, list[Note]] = defaultdict(list)
    for note in vault.notes:
        if note.frontmatter == "none":
            add("VR003", "error", note, 1, "no frontmatter")
        elif note.frontmatter == "broken":
            add("VR003", "error", note, 1, "frontmatter is not a valid YAML mapping")
        else:
            for key in cfg.required_fields:
                value = {"name": note.name, "description": note.description}.get(
                    key, note.meta.get(key)
                )
                if value in (None, "", [], {}):
                    add("VR003", "error", note, 1, f"missing required field '{key}'")
        if note.name:
            by_name[note.name].append(note)
            if note.name != note.stem:
                add("VR004", "warning", note, 1, f"name '{note.name}' differs from file name")
    for name, group in by_name.items():
        if len(group) > 1:
            for note in group:
                others = ", ".join(n.rel for n in group if n is not note)
                add("VR005", "error", note, 1, f"name '{name}' is also used by {others}")


def _check_orphans(vault: Vault, resolver: Resolver, add) -> None:
    linked = resolver.inbound()
    for note in vault.notes:
        if note.path not in linked:
            add("VR006", "info", note, None, "no note or index links here")


def _check_indexes(cfg: Config, vault: Vault, add) -> None:
    for note in vault.indexes:
        size = note.path.stat().st_size
        if size > cfg.budget_hard:
            add("VR007", "error", note, None, f"{size} bytes exceeds hard budget {cfg.budget_hard}")
        elif size > cfg.budget_soft:
            add(
                "VR007",
                "warning",
                note,
                None,
                f"{size} bytes exceeds soft budget {cfg.budget_soft}",
            )
        lines = note.body.split("\n")
        for lineno, text in enumerate(lines, start=note.body_line):
            if len(text) > cfg.max_index_line:
                add(
                    "VR010",
                    "warning",
                    note,
                    lineno,
                    f"line has {len(text)} characters (max {cfg.max_index_line})",
                )


def _check_near_duplicates(cfg: Config, vault: Vault, vectors, add) -> None:
    import numpy as np

    notes: list[Note] = sorted((n for n in vault.notes if n.rel in vectors), key=lambda n: n.rel)
    if len(notes) < 2:
        return
    matrix = np.vstack([vectors[n.rel] for n in notes])
    sims = matrix @ matrix.T
    for i, j in zip(*np.where(np.triu(sims >= cfg.duplicate_similarity, k=1)), strict=True):
        add(
            "VR008",
            "warning",
            notes[i],
            None,
            f"near-duplicate of {notes[j].rel} (cosine {sims[i, j]:.2f})",
        )


def exit_code(findings: list[Finding], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = LEVELS[fail_on]
    return int(any(LEVELS[f.level] >= threshold for f in findings))


def summarise(findings: list[Finding]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        counts[f.level] += 1
    return counts


def format_human(findings: list[Finding]) -> str:
    if not findings:
        return "No problems found."
    out: list[str] = []
    current = None
    for f in findings:
        if f.path != current:
            out.append(f.path)
            current = f.path
        line = f"L{f.line}" if f.line else ""
        out.append(f"  {line:<5} {f.code}  {f.level:<7}  {f.message}")
    counts = summarise(findings)
    out.append("")
    out.append(f"{counts['error']} errors, {counts['warning']} warnings, {counts['info']} notes")
    return "\n".join(out)
