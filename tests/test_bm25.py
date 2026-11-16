from markdown_memory_mcp.bm25 import BM25


def docs(*texts):
    return [t.split() for t in texts]


def test_matching_document_ranks_first():
    bm = BM25(docs("sourdough starter feeding", "nginx cache headers", "backup before migration"))
    scores = bm.scores(["starter"])
    assert scores[0] > 0
    assert scores[1] == scores[2] == 0


def test_rarer_terms_weigh_more():
    bm = BM25(docs("cache cache cache", "cache rare", "cache plain", "cache other"))
    assert bm.scores(["rare"])[1] > bm.scores(["cache"])[1]


def test_shorter_document_wins_on_equal_term_frequency():
    bm = BM25(docs("starter", "starter " + "filler " * 40, "other words here"))
    scores = bm.scores(["starter"])
    assert scores[0] > scores[1] > scores[2]


def test_repeated_query_terms_count_once():
    bm = BM25(docs("alpha beta", "gamma delta"))
    assert bm.scores(["alpha", "alpha"]) == bm.scores(["alpha"])


def test_empty_corpus_and_unknown_terms():
    assert BM25([]).scores(["x"]) == []
    assert BM25(docs("a b")).scores(["zzz"]) == [0.0]
