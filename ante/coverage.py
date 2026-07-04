# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Coverage map + abstention rule.

Coverage answers Principle 3's "coverage is not mastery" honestly: it reports how much
of the exam the deck even *touches*, weighted by exam value, and tells the
readiness model when to refuse a score. A 50k-card deck that skips a high-weight
section must not look "ready".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .outline import Outline, load_outline

# A section counts as "high weight" (and therefore must not be near-empty before
# we trust a score) when its exam multiplier is at least this.
HIGH_WEIGHT_CUTOFF = 1.10
# A high-weight section below this fraction is treated as a blind spot.
SECTION_BLIND_SPOT = 0.20
# Minimum weighted coverage before a readiness score is allowed at all.
DEFAULT_MIN_COVERAGE = 0.50


@dataclass(frozen=True)
class SectionCoverage:
    id: str
    name: str
    weight: float
    covered: int
    total: int

    @property
    def fraction(self) -> float:
        return self.covered / self.total if self.total else 0.0


@dataclass(frozen=True)
class CoverageReport:
    overall_coverage: float
    weighted_coverage: float
    covered_topics: int
    total_topics: int
    sections: list[SectionCoverage]
    missing_high_weight_sections: list[str] = field(default_factory=list)

    def abstains(self, min_coverage: float = DEFAULT_MIN_COVERAGE) -> bool:
        """Whether the readiness model should refuse to show a score."""
        return self.weighted_coverage < min_coverage or bool(
            self.missing_high_weight_sections
        )

    def reasons(self, min_coverage: float = DEFAULT_MIN_COVERAGE) -> list[str]:
        out: list[str] = []
        if self.weighted_coverage < min_coverage:
            out.append(
                f"weighted coverage {self.weighted_coverage:.0%} is below the "
                f"{min_coverage:.0%} minimum"
            )
        for sid in self.missing_high_weight_sections:
            out.append(f"high-weight section '{sid}' is essentially uncovered")
        return out


def compute_coverage(
    topic_card_counts: dict[str, int],
    outline: Outline | None = None,
    min_cards: int = 1,
) -> CoverageReport:
    """Build a coverage report from per-topic card counts.

    ``topic_card_counts`` maps full topic tags (as returned by the GetTopicMastery
    RPC) to the number of cards in the deck for that topic. A topic is "covered"
    when it has at least ``min_cards`` cards.
    """
    outline = outline or load_outline()

    sections: list[SectionCoverage] = []
    covered_total = 0
    weighted_covered = 0.0
    weighted_all = 0.0
    missing_high: list[str] = []

    for section in outline.sections:
        covered = sum(
            1 for t in section.topics if topic_card_counts.get(t, 0) >= min_cards
        )
        total = len(section.topics)
        covered_total += covered
        weighted_all += section.weight * total
        weighted_covered += section.weight * covered
        sc = SectionCoverage(
            id=section.id,
            name=section.name,
            weight=section.weight,
            covered=covered,
            total=total,
        )
        sections.append(sc)
        if section.weight >= HIGH_WEIGHT_CUTOFF and sc.fraction < SECTION_BLIND_SPOT:
            missing_high.append(section.id)

    total_topics = len(outline.all_topics())
    return CoverageReport(
        overall_coverage=covered_total / total_topics if total_topics else 0.0,
        weighted_coverage=weighted_covered / weighted_all if weighted_all else 0.0,
        covered_topics=covered_total,
        total_topics=total_topics,
        sections=sections,
        missing_high_weight_sections=missing_high,
    )


def topic_counts_from_mastery(mastery_response) -> dict[str, int]:
    """Adapt a GetTopicMasteryResponse into the {topic: card_count} map."""
    return {t.topic: t.total_cards for t in mastery_response.topics}
