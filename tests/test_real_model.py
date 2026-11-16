"""Runs the real default model. Downloads it on first use, so it is skipped by default:
``uv run pytest -m slow``.
"""

from __future__ import annotations

import pytest

from markdown_memory_mcp.config import DEFAULT_MODEL
from markdown_memory_mcp.embed import FastEmbedder
from markdown_memory_mcp.search import build_corpus
from markdown_memory_mcp.store import Store, cache_root
from markdown_memory_mcp.vault import load_vault

pytestmark = pytest.mark.slow


def test_meaning_beats_exact_words(cfg):
    embedder = FastEmbedder(DEFAULT_MODEL, cache_root() / "models", local_only=False)
    Store(cfg.vault, cfg.model).update(load_vault(cfg).notes, embedder)
    corpus = build_corpus(cfg, lambda: embedder)
    assert corpus.hybrid

    # No word of the query appears in the note.
    top = corpus.search("how do I keep my bread culture alive", k=3)
    assert "sourdough-starter-feeding" in [h.note.stem for h in top]

    # Turkish query against the Turkish note.
    top = corpus.search("simit nasıl yapılır", k=1)[0]
    assert top.note.stem == "istanbul-simit"


@pytest.mark.parametrize(
    "prompt",
    [
        "how do I keep the starter alive",
        "my starter smells like nail polish, what now",
    ],
)
def test_hook_injects_with_default_thresholds(cfg, prompt):
    import json

    from markdown_memory_mcp import hook

    embedder = FastEmbedder(DEFAULT_MODEL, cache_root() / "models", local_only=False)
    Store(cfg.vault, cfg.model).update(load_vault(cfg).notes, embedder)
    out = hook.handle({"prompt": prompt, "session_id": prompt}, cfg, lambda: embedder)
    assert out is not None
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "sourdough-starter-feeding" in context
