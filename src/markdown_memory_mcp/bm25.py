"""Okapi BM25 in pure Python."""

from __future__ import annotations

import math
from collections import Counter

K1 = 1.2
B = 0.75


class BM25:
    def __init__(self, documents: list[list[str]], k1: float = K1, b: float = B) -> None:
        self.k1 = k1
        self.b = b
        self.size = len(documents)
        self._tf = [Counter(doc) for doc in documents]
        self._len = [len(doc) for doc in documents]
        self._avg_len = sum(self._len) / self.size if self.size else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        # The "+1" inside the log keeps idf positive for terms present in most documents.
        self._idf = {
            term: math.log(1 + (self.size - n + 0.5) / (n + 0.5)) for term, n in df.items()
        }

    def scores(self, query: list[str]) -> list[float]:
        """Score of every document for the query. Repeated query terms count once."""
        terms = [t for t in dict.fromkeys(query) if t in self._idf]
        out = [0.0] * self.size
        if not terms or not self._avg_len:
            return out
        for i, tf in enumerate(self._tf):
            norm = self.k1 * (1 - self.b + self.b * self._len[i] / self._avg_len)
            total = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if f:
                    total += self._idf[term] * f * (self.k1 + 1) / (f + norm)
            out[i] = total
        return out
