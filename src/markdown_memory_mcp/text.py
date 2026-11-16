"""Text normalisation shared by BM25 and the hook's skip rules.

Folding is deliberately crude. The same pipeline runs on documents and queries, so a
consistent fold matters more than linguistic correctness.
"""

from __future__ import annotations

import re
import unicodedata

STEM_LENGTH = 5

# str.lower() maps "I" to "i" but leaves the Turkish capital dotted I (U+0130) as "i" plus a
# combining dot, and the dotless small i (U+0131) as it is. Mapping all three to a plain "i"
# makes "ISTANBUL", the dotted-capital spelling and "istanbul" the same token.
_I_FOLD = str.maketrans({"\u0130": "i", "I": "i", "\u0131": "i"})
_WORD = re.compile(r"\w+")


def fold(text: str) -> str:
    """NFC-normalise, map the dotted and dotless I variants to "i", lowercase."""
    return unicodedata.normalize("NFC", text).translate(_I_FOLD).lower()


def words(text: str) -> list[str]:
    """Folded word tokens of any length, with underscores treated as separators."""
    return _WORD.findall(fold(text).replace("_", " "))


def stem(token: str) -> str:
    """Prefix stem. Works acceptably for agglutinative languages such as Turkish."""
    return token[:STEM_LENGTH]


def tokenize(text: str) -> list[str]:
    """Stemmed tokens of length >= 2, in order and with repeats."""
    return [stem(w) for w in words(text) if len(w) >= 2]
