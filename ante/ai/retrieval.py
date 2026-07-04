# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Retrieval over chunked source text (PRD 8.1).

The AI pipeline does not dump a whole textbook into the model. It chunks the
source (keeping char-span source_refs), retrieves the chunks most relevant to a
target topic, and generates only from those. This both improves quality and is
the thing we must show beats a simpler baseline (keyword vs vector retrieval,
PRD 8.3). Dependency-free: reuses the TF-IDF retriever from baseline.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .baseline import TfidfRetriever, keyword_best, tokenize

_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    start: int
    end: int
    index: int

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "index": self.index,
        }


@dataclass
class _Sentence:
    text: str
    start: int
    end: int


def _sentences_with_spans(source: str) -> list[_Sentence]:
    """Sentence segmentation that preserves exact char spans into the source."""
    out: list[_Sentence] = []
    cursor = 0
    for para in _PARA_SPLIT.split(source):
        if not para.strip():
            continue
        base = source.find(para, cursor)
        if base < 0:
            base = cursor
        cursor = base + len(para)
        inner = base
        for sent in _SENT_SPLIT.split(para):
            if not sent.strip():
                continue
            s_start = source.find(sent, inner)
            if s_start < 0:
                s_start = inner
            s_end = s_start + len(sent)
            inner = s_end
            out.append(_Sentence(sent.strip(), s_start, s_end))
    return out


def chunk_text(
    source: str, target_chars: int = 500, overlap_sentences: int = 1
) -> list[Chunk]:
    """Split a source into ~target_chars chunks on sentence boundaries, keeping
    exact char spans for provenance."""
    sentences = _sentences_with_spans(source)
    chunks: list[Chunk] = []
    cur: list[_Sentence] = []
    cur_len = 0
    for s in sentences:
        if cur and cur_len + len(s.text) > target_chars:
            start, end = cur[0].start, cur[-1].end
            chunks.append(Chunk(source[start:end].strip(), start, end, len(chunks)))
            keep = cur[-overlap_sentences:] if overlap_sentences else []
            cur = list(keep)
            cur_len = sum(len(x.text) for x in keep)
        cur.append(s)
        cur_len += len(s.text)
    if cur:
        start, end = cur[0].start, cur[-1].end
        chunks.append(Chunk(source[start:end].strip(), start, end, len(chunks)))
    return chunks


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


class ChunkRetriever:
    """TF-IDF retrieval over source chunks, with a keyword fallback baseline."""

    def __init__(self, source: str, target_chars: int = 500):
        self.source = source
        self.chunks = chunk_text(source, target_chars)
        self._texts = [c.text for c in self.chunks]
        self._tfidf = TfidfRetriever(self._texts) if self._texts else None

    def retrieve(self, query: str, k: int = 3) -> list[RetrievedChunk]:
        if not self.chunks:
            return []
        qv = self._tfidf._vec(tokenize(query)) if self._tfidf else {}
        scored = []
        for i, c in enumerate(self.chunks):
            dv = self._tfidf.doc_vecs[i] if self._tfidf else {}
            score = sum(qv.get(t, 0.0) * dv.get(t, 0.0) for t in qv)
            scored.append(RetrievedChunk(c, score))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]

    def retrieve_keyword_baseline(self, query: str) -> Chunk | None:
        """The simpler baseline the AI retrieval must beat (PRD 8.3)."""
        if not self.chunks:
            return None
        return self.chunks[keyword_best(query, self._texts)]


def topic_query(topic_tag: str) -> str:
    """Turn a topic tag into a retrieval query (drop the prefix, spread words)."""
    body = topic_tag.split("::", 1)[1] if "::" in topic_tag else topic_tag
    return body.replace("::", " ").replace("_", " ")
