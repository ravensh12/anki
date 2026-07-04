# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Card generation from a source, with provenance and an injection guard."""

from __future__ import annotations

from dataclasses import dataclass

from .injection import sanitize_source
from .provider import GeneratedCard, Provider, get_provider
from .retrieval import ChunkRetriever, topic_query


@dataclass(frozen=True)
class GenerationResult:
    cards: list[GeneratedCard]
    generator: str
    source_id: str
    rejected_reason: str | None = None
    injection_flags: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "generator": self.generator,
            "source_id": self.source_id,
            "rejected_reason": self.rejected_reason,
            "injection_flags": self.injection_flags or [],
            "cards": [c.as_dict() for c in self.cards],
        }


def generate_cards(
    source: str,
    source_id: str = "source",
    max_cards: int = 20,
    provider: Provider | None = None,
) -> GenerationResult:
    """Generate cards from a source string. Refuses hostile sources; every card
    carries the source id, char span, and exact supporting quote."""
    provider = provider or get_provider()
    san = sanitize_source(source)
    if san.hostile:
        return GenerationResult(
            cards=[],
            generator=provider.name,
            source_id=source_id,
            rejected_reason="source flagged as a likely prompt-injection attempt",
            injection_flags=san.flagged,
        )
    cards = provider.generate_cards(san.text, source_id, max_cards)
    return GenerationResult(
        cards=cards,
        generator=provider.name,
        source_id=source_id,
        injection_flags=san.flagged,
    )


def generate_cards_for_topic(
    source: str,
    topic_tag: str,
    source_id: str = "source",
    max_cards: int = 10,
    k_chunks: int = 3,
    provider: Provider | None = None,
) -> GenerationResult:
    """Retrieval-augmented generation (PRD 8.1): chunk the source, retrieve the
    chunks most relevant to ``topic_tag``, and generate only from those. Each
    card still carries a traceable source span (offset into the full source)."""
    provider = provider or get_provider()
    san = sanitize_source(source)
    if san.hostile:
        return GenerationResult(
            cards=[],
            generator=provider.name,
            source_id=source_id,
            rejected_reason="source flagged as a likely prompt-injection attempt",
            injection_flags=san.flagged,
        )

    retriever = ChunkRetriever(san.text)
    retrieved = retriever.retrieve(topic_query(topic_tag), k=k_chunks)
    cards: list[GeneratedCard] = []
    for rc in retrieved:
        sub = provider.generate_cards(rc.chunk.text, source_id, max_cards)
        for c in sub:
            # rebase the span onto the full source for honest provenance
            base = rc.chunk.start
            cards.append(
                GeneratedCard(
                    front=c.front,
                    back=c.back,
                    source_id=source_id,
                    source_span=(base + c.source_span[0], base + c.source_span[1]),
                    source_quote=c.source_quote,
                    generator=c.generator,
                )
            )
            if len(cards) >= max_cards:
                break
        if len(cards) >= max_cards:
            break
    return GenerationResult(
        cards=cards,
        generator=provider.name,
        source_id=source_id,
        injection_flags=san.flagged,
    )
