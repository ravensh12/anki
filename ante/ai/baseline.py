# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Simple, dependency-free baselines the AI must beat: keyword overlap and
TF-IDF cosine retrieval. Used by the eval harness for the side-by-side."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOK = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "and",
    "or",
    "is",
    "are",
    "was",
    "were",
    "for",
    "with",
    "as",
    "by",
    "that",
    "this",
    "it",
    "its",
    "at",
    "be",
    "what",
    "which",
    "does",
    "do",
    "the",
}


def tokenize(text: str) -> list[str]:
    return [w for w in _TOK.findall(text.lower()) if w not in _STOP]


def keyword_best(query: str, candidates: list[str]) -> int:
    """Index of the candidate with the highest raw token overlap."""
    q = set(tokenize(query))
    best, best_score = 0, -1
    for i, c in enumerate(candidates):
        score = len(q & set(tokenize(c)))
        if score > best_score:
            best, best_score = i, score
    return best


class TfidfRetriever:
    def __init__(self, documents: list[str]) -> None:
        self.docs = documents
        self.doc_tokens = [tokenize(d) for d in documents]
        df: Counter[str] = Counter()
        for toks in self.doc_tokens:
            for t in set(toks):
                df[t] += 1
        n = len(documents) or 1
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.doc_vecs = [self._vec(toks) for toks in self.doc_tokens]

    def _vec(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        vec = (
            {t: (c / len(tokens)) * self.idf.get(t, 0.0) for t, c in tf.items()}
            if tokens
            else {}
        )
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def best(self, query: str) -> int:
        qv = self._vec(tokenize(query))
        best, best_score = 0, -1.0
        for i, dv in enumerate(self.doc_vecs):
            score = sum(qv.get(t, 0.0) * dv.get(t, 0.0) for t in qv)
            if score > best_score:
                best, best_score = i, score
        return best
