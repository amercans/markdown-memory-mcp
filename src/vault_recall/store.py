"""On-disk embedding index. It lives in a cache directory, never inside the vault."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .embed import Embedder
from .vault import Note

if TYPE_CHECKING:
    import numpy as np

FORMAT_VERSION = 2


class StoreError(Exception):
    pass


def cache_root(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("VAULT_RECALL_CACHE"):
        return Path(env["VAULT_RECALL_CACHE"]).expanduser()
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]).expanduser() / "vault-recall"
    return Path.home() / ".cache" / "vault-recall"


def private_dir(path: Path) -> None:
    """Create ``path`` and any missing parents with mode 0700. Existing directories are kept."""
    missing = []
    for candidate in (path, *path.parents):
        if candidate.exists():
            break
        missing.append(candidate)
    for directory in reversed(missing):
        with contextlib.suppress(FileExistsError):
            directory.mkdir(mode=0o700)


def atomic_write(path: Path, writer) -> None:
    """Write through a temporary file in the same directory, then replace.

    The file is private (mode 0600) and so are directories created on the way.
    """
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            writer(handle)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class Changes:
    added: list[Note]
    modified: list[Note]
    removed: list[str]

    @property
    def to_embed(self) -> list[Note]:
        return [*self.added, *self.modified]

    @property
    def count(self) -> int:
        return len(self.added) + len(self.modified) + len(self.removed)


@dataclass(frozen=True)
class UpdateResult:
    added: int
    modified: int
    removed: int
    unchanged: int


class Store:
    def __init__(self, vault: Path, model: str, root: Path | None = None) -> None:
        vault = vault.resolve()
        self.root = (root or cache_root()).expanduser().resolve()
        if self.root == vault or vault in self.root.parents:
            raise StoreError(f"cache directory {self.root} is inside the vault")
        key = hashlib.sha256(f"{vault}\0{model}".encode()).hexdigest()[:16]
        self.vault = vault
        self.model = model
        self.dir = self.root / key
        self._vectors = self.dir / "vectors.npy"
        self._meta = self.dir / "meta.json"

    def exists(self) -> bool:
        return self._meta.is_file() and self._vectors.is_file()

    def _read(self) -> tuple[dict[str, dict], np.ndarray] | None:
        """Stored entries (by relative path) and the vector matrix, or None if unusable."""
        import numpy as np

        if not self.exists():
            return None
        try:
            meta = json.loads(self._meta.read_text(encoding="utf-8"))
            raw = self._vectors.read_bytes()
            # The two files are replaced one after the other. If a crash fell between the
            # writes, the hash recorded in meta.json no longer matches vectors.npy.
            if hashlib.sha256(raw).hexdigest() != meta.get("vectors_sha256"):
                return None
            vectors = np.load(io.BytesIO(raw), allow_pickle=False)
        except (OSError, ValueError, AttributeError):
            return None
        if meta.get("version") != FORMAT_VERSION or meta.get("model") != self.model:
            return None
        files = meta.get("files")
        if not isinstance(files, list) or vectors.ndim != 2 or vectors.shape[0] != len(files):
            return None
        try:
            entries = {f["path"]: f for f in files}
            valid = all(0 <= f["row"] < len(files) and f["sha256"] for f in files)
        except (KeyError, TypeError):
            return None
        return (entries, vectors) if valid else None

    def changes(self, notes: list[Note] | tuple[Note, ...]) -> Changes:
        stored = self._read()
        entries = stored[0] if stored else {}
        added, modified = [], []
        for note in notes:
            entry = entries.get(note.rel)
            if entry is None:
                added.append(note)
            elif entry["sha256"] != note.sha256:
                modified.append(note)
        current = {n.rel for n in notes}
        return Changes(added, modified, sorted(set(entries) - current))

    def vectors_for(self, notes: list[Note] | tuple[Note, ...]) -> dict[str, np.ndarray]:
        """Vectors of the notes whose stored hash still matches the file."""
        stored = self._read()
        if stored is None:
            return {}
        entries, vectors = stored
        return {
            n.rel: vectors[entries[n.rel]["row"]]
            for n in notes
            if n.rel in entries and entries[n.rel]["sha256"] == n.sha256
        }

    def update(
        self, notes: list[Note] | tuple[Note, ...], embedder: Embedder | None
    ) -> UpdateResult:
        """Bring the index in line with ``notes``, embedding only what changed."""
        import numpy as np

        changes = self.changes(notes)
        fresh = self.vectors_for(notes)
        if changes.to_embed:
            if embedder is None:
                raise StoreError("an embedder is required to index new or changed notes")
            new = embedder.embed([n.embed_text() for n in changes.to_embed])
            fresh.update({n.rel: new[i] for i, n in enumerate(changes.to_embed)})
        ordered = [n for n in notes if n.rel in fresh]
        matrix = (
            np.vstack([fresh[n.rel] for n in ordered]).astype(np.float32)
            if ordered
            else np.zeros((0, 0), dtype=np.float32)
        )
        buffer = io.BytesIO()
        np.save(buffer, matrix, allow_pickle=False)
        payload = buffer.getvalue()
        meta = {
            "version": FORMAT_VERSION,
            "model": self.model,
            "vault": str(self.vault),
            "dim": int(matrix.shape[1]) if ordered else 0,
            "vectors_sha256": hashlib.sha256(payload).hexdigest(),
            "files": [
                {"path": n.rel, "mtime": n.mtime, "sha256": n.sha256, "row": i}
                for i, n in enumerate(ordered)
            ],
        }
        if changes.count or not self.exists():
            atomic_write(self._vectors, lambda fh: fh.write(payload))
            atomic_write(self._meta, lambda fh: fh.write(json.dumps(meta, indent=1).encode()))
        return UpdateResult(
            added=len(changes.added),
            modified=len(changes.modified),
            removed=len(changes.removed),
            unchanged=len(ordered) - len(changes.to_embed),
        )
