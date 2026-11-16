from __future__ import annotations

from conftest import write_note
from vault_recall.search import build_corpus, rrf_fuse
from vault_recall.store import Store
from vault_recall.vault import load_vault


def test_rrf_fuse_sums_reciprocal_ranks():
    fused = rrf_fuse([[1, 2, 3], [3, 1]], k=60)
    assert fused[1] == 1 / 61 + 1 / 62
    assert fused[3] == 1 / 63 + 1 / 61
    assert fused[2] == 1 / 62
    assert sorted(fused, key=lambda i: -fused[i]) == [1, 3, 2]


def test_rrf_item_in_both_lists_beats_item_top_in_one():
    fused = rrf_fuse([[10, 20], [30, 20]])
    assert max(fused, key=fused.get) == 20


def indexed(cfg, embedder):
    Store(cfg.vault, cfg.model).update(load_vault(cfg).notes, embedder)
    return build_corpus(cfg, lambda: embedder)


def test_bm25_only_when_no_embedder(cfg):
    corpus = build_corpus(cfg, None)
    assert not corpus.hybrid
    hits = corpus.search("sourdough starter", k=3)
    assert hits[0].note.stem == "sourdough-starter-feeding"
    assert all(h.cosine is None for h in hits)


def test_bm25_only_when_index_missing(cfg, embedder):
    corpus = build_corpus(cfg, lambda: embedder)
    assert corpus is not None and not corpus.hybrid


def test_require_index_returns_none_without_index(cfg, embedder):
    assert build_corpus(cfg, lambda: embedder, require_index=True) is None
    assert build_corpus(cfg, None, require_index=True) is not None


def test_hybrid_returns_score_breakdown(cfg, embedder):
    corpus = indexed(cfg, embedder)
    assert corpus.hybrid
    hit = corpus.search("how often should I feed the sourdough starter", k=1)[0]
    assert hit.note.stem == "sourdough-starter-feeding"
    assert hit.cosine is not None and hit.bm25 > 0 and hit.rrf > 0
    assert set(hit.to_dict()) == {"name", "path", "description", "rrf", "cosine", "bm25"}


def test_semantic_ranking_contributes_when_no_word_matches(cfg, embedder):
    corpus = indexed(cfg, embedder)
    hits = corpus.search("zzzzqq", k=None)
    assert len(hits) == len(corpus.notes)  # the embedding list ranks every note
    assert all(h.bm25 == 0 for h in hits)


def test_turkish_folding_finds_the_simit_note(cfg):
    corpus = build_corpus(cfg, None)
    top = corpus.search("ISTANBUL simit", k=1)[0]
    assert top.note.stem == "istanbul-simit"


def test_exclude_removes_notes_before_ranking(cfg):
    corpus = build_corpus(cfg, None)
    hits = corpus.search("sourdough starter", k=None, exclude={"sourdough-starter-feeding.md"})
    assert all(h.note.stem != "sourdough-starter-feeding" for h in hits)


def test_title_words_get_a_boost(cfg, vault_dir):
    write_note(vault_dir, "zebra-crossing.md", "---\nname: zebra-crossing\n---\nunrelated body")
    write_note(vault_dir, "other.md", "---\nname: other\n---\nzebra " + "word " * 3)
    hits = build_corpus(cfg, None).search("zebra", k=2)
    assert hits[0].note.stem == "zebra-crossing"


def test_stale_notes_are_ranked_lexically_only(cfg, embedder, vault_dir):
    indexed(cfg, embedder)
    write_note(vault_dir, "late-addition.md", "---\nname: late-addition\n---\nquokka habitat")
    corpus = build_corpus(cfg, lambda: embedder, max_updates=0)
    assert corpus.stale == 1
    hit = next(h for h in corpus.search("quokka", k=3) if h.note.stem == "late-addition")
    assert hit.cosine is None and hit.bm25 > 0
    fresh = build_corpus(cfg, lambda: embedder, max_updates=5)
    assert fresh.stale == 0
