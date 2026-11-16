"""Embedding backends. numpy and fastembed are optional (``vault-recall[embed]``)."""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .text import tokenize

if TYPE_CHECKING:
    import numpy as np

INSTALL_COMMAND = (
    'uv tool install "vault-recall[all] @ git+https://github.com/amercans/vault-recall"'
)
INSTALL_HINT = f"install the extras with: {INSTALL_COMMAND}"


class EmbedUnavailable(Exception):
    """The embedding extra is not installed, or the model cannot be loaded."""


class Embedder(Protocol):
    """Turns texts into L2-normalised float32 vectors, one row per text."""

    def embed(self, texts: list[str]) -> np.ndarray: ...


EmbedderFactory = Callable[[], Embedder]


def embeddings_installed() -> bool:
    return all(importlib.util.find_spec(m) for m in ("numpy", "fastembed"))


def l2_normalise(matrix: np.ndarray) -> np.ndarray:
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class HashingEmbedder:
    """Deterministic bag-of-stems embedder. Needs no model, so tests never download one."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        import numpy as np

        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in tokenize(text):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dim
                out[row, bucket] += 1.0 if digest[4] & 1 else -1.0
        return l2_normalise(out)


class FastEmbedder:
    def __init__(self, model: str, cache_dir: Path, local_only: bool) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbedUnavailable(INSTALL_HINT) from exc
        try:
            self._model = TextEmbedding(
                model_name=model, cache_dir=str(cache_dir), local_files_only=local_only
            )
        except Exception as exc:
            raise EmbedUnavailable(f"cannot load model {model!r}: {exc}") from exc

    def embed(self, texts: list[str]) -> np.ndarray:
        import numpy as np

        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return l2_normalise(np.vstack(list(self._model.embed(texts))))


def default_factory(model: str, cache_root: Path, *, local_only: bool) -> EmbedderFactory | None:
    """A factory for the real model, or ``None`` when the extra is not installed.

    ``local_only`` makes loading fail instead of downloading the model.
    """
    if not embeddings_installed():
        return None
    return lambda: FastEmbedder(model, cache_root / "models", local_only)
