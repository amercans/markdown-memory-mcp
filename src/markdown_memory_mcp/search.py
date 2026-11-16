"""Hybrid ranking: BM25 and embedding cosine, fused with Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .bm25 import BM25
from .config import Config
from .embed import Embedder, EmbedderFactory, EmbedUnavailable
from .store import Store
from .text import tokenize
from .vault import Note, Vault, load_vault

if TYPE_CHECKING:
    import numpy as np

RRF_K = 60
QUERY_CHARS = 1000


@dataclass(frozen=True)
class Hit:
    note: Note
    rrf: float
    cosine: float | None
    bm25: float

    def to_dict(self) -> dict:
        return {
            "name": self.note.label,
            "path": self.note.rel,
            "description": self.note.description,
            "rrf": round(self.rrf, 6),
            "cosine": None if self.cosine is None else round(self.cosine, 4),
            "bm25": round(self.bm25, 4),
        }


def rrf_fuse(rankings: Iterable[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion over ranked lists of item ids (best first)."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + position)
    return fused


@dataclass
class Corpus:
    vault: Vault
    bm25: BM25
    vectors: dict[str, np.ndarray]  # by relative path; only notes whose hash matches
    embedder: Embedder | None
    stale: int = 0  # notes whose vector is missing or out of date
    notice: str | None = None  # why embeddings are not used, when an index exists

    @property
    def notes(self) -> tuple[Note, ...]:
        return self.vault.notes

    @property
    def hybrid(self) -> bool:
        return self.embedder is not None and bool(self.vectors)

    def search(
        self,
        query: str,
        k: int | None = 5,
        exclude: set[str] | None = None,
    ) -> list[Hit]:
        """Hits best first. ``exclude`` holds relative paths that must not take part at all.

        With ``k=None`` every ranked note is returned, so callers can filter before cutting.
        """
        notes = self.notes
        exclude = exclude or set()
        allowed = [i for i, n in enumerate(notes) if n.rel not in exclude]

        scores = self.bm25.scores(tokenize(query))
        lexical = sorted((i for i in allowed if scores[i] > 0), key=lambda i: -scores[i])

        cosines: dict[int, float] = {}
        if self.hybrid and self.embedder is not None:
            import numpy as np  # only the embedding path needs it, so the core install works

            q = self.embedder.embed([query[:QUERY_CHARS]])[0]
            for i in allowed:
                vec = self.vectors.get(notes[i].rel)
                if vec is not None:
                    cosines[i] = float(np.dot(vec, q))
        semantic = sorted(cosines, key=lambda i: -cosines[i])

        fused = rrf_fuse([semantic, lexical])
        order = sorted(
            fused,
            key=lambda i: (-fused[i], -cosines.get(i, -1.0), -scores[i], notes[i].rel),
        )
        hits = [Hit(notes[i], fused[i], cosines.get(i), scores[i]) for i in order]
        return hits if k is None else hits[:k]


def build_corpus(
    cfg: Config,
    factory: EmbedderFactory | None,
    *,
    max_updates: int | None = None,
    require_index: bool = False,
    store: Store | None = None,
) -> Corpus | None:
    """Load the vault and, when an embedding index exists, bring it up to date.

    ``max_updates`` caps how many changed files are re-embedded; beyond it the index is used
    as it is. ``require_index`` returns ``None`` instead of a BM25-only corpus when embeddings
    are installed but no index has been built.
    """
    vault = load_vault(cfg)
    bm25 = BM25([tokenize(n.rank_text()) for n in vault.notes])
    corpus = Corpus(vault, bm25, {}, None)
    if factory is None:
        return corpus
    store = store or Store(cfg.vault, cfg.model)
    if not store.exists():
        return None if require_index else corpus
    try:
        embedder = factory()
    except EmbedUnavailable as exc:
        if require_index:
            raise
        corpus.notice = str(exc)
        return corpus
    changes = store.changes(vault.notes)
    if changes.count and (max_updates is None or changes.count <= max_updates):
        store.update(vault.notes, embedder)
    corpus.embedder = embedder
    corpus.vectors = store.vectors_for(vault.notes)
    corpus.stale = len(vault.notes) - len(corpus.vectors)
    return corpus
