from markdown_memory_mcp.text import fold, stem, tokenize, words


def test_fold_maps_dotted_and_dotless_i():
    assert fold("İstanbul") == "istanbul"
    assert fold("ISTANBUL") == "istanbul"
    assert fold("ısı") == "isi"


def test_fold_composes_combining_dot():
    # "I" followed by a combining dot above is the same letter as U+0130 after NFC.
    assert fold("İstanbul") == "istanbul"


def test_tokenize_drops_single_characters_and_stems():
    assert tokenize("A cache-control header") == ["cache", "contr", "heade"]


def test_stem_is_a_five_character_prefix():
    assert stem("kitaplarımızdan") == "kitap"
    assert stem("go") == "go"


def test_underscores_separate_words():
    assert words("snake_case_name") == ["snake", "case", "name"]


def test_turkish_inflections_share_a_stem():
    assert tokenize("simitler")[0] == tokenize("SİMİT")[0] == "simit"


def test_empty_and_punctuation_only():
    assert tokenize("") == []
    assert tokenize("-- !! ??") == []
