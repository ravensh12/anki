# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Readiness model: performance -> MCAT score, honestly.

This is the layer the spec is most strict about. It never blends the three
signals: it consumes the **performance** model's per-section accuracy (the chance
of getting a new exam-style question right), not raw memory recall, and turns it
into a projected MCAT score with a range, a confidence level, the reasons behind
it, and an explicit give-up rule.

Give-up rule (written down, per spec): we show NO score unless
  * there are at least MIN_REVIEWS graded reviews, and
  * weighted topic coverage is at least MIN_COVERAGE with no high-weight blind
    spot (see ante.coverage).
"A good system knows when it does not know."

The score mapping is a documented linear heuristic, not a validated concordance.
We say so. Per spec section 9, an honest "we can't yet prove the projected score"
beats a polished number we can't back up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .coverage import DEFAULT_MIN_COVERAGE, CoverageReport
from .outline import Outline, load_outline

# Give-up thresholds.
MIN_REVIEWS = 200
MIN_COVERAGE = DEFAULT_MIN_COVERAGE

# Performance assumed on a topic the student has NOT studied. Novel MCAT
# application items are 4-option, so blind guessing floors near 0.25; we use a
# conservative point with a wide interval so uncovered sections read as uncertain.
UNCOVERED_PRIOR = (0.20, 0.05, 0.40)

# MCAT scale.
SECTION_MIN = 118
SECTION_MAX = 132
SECTION_SPAN = SECTION_MAX - SECTION_MIN  # 14
N_SECTIONS = 4  # bio, chem/phys, psych/soc, cars


@dataclass(frozen=True)
class SectionScore:
    section: str
    accuracy: float
    score: int
    low: int
    high: int


@dataclass(frozen=True)
class ReadinessReport:
    abstained: bool
    reasons: list[str]
    projected_total: int | None
    total_low: int | None
    total_high: int | None
    confidence: str | None
    coverage: float
    n_reviews: int
    sections: list[SectionScore]
    best_next_topic: str | None
    overconfidence_applied: float = 0.0
    updated_at: float = field(default_factory=time.time)
    method: str = (
        "Linear map: section score = 118 + 14*accuracy, clamped to [118,132]; "
        "total = sum of four sections. Heuristic, not an AAMC concordance."
    )

    def as_dict(self) -> dict:
        return {
            "abstained": self.abstained,
            "reasons": self.reasons,
            "projected_total": self.projected_total,
            "total_range": [self.total_low, self.total_high]
            if self.projected_total is not None
            else None,
            "confidence": self.confidence,
            "overconfidence_applied": round(self.overconfidence_applied, 4),
            "coverage": self.coverage,
            "n_reviews": self.n_reviews,
            "sections": [
                {
                    "section": s.section,
                    "accuracy": s.accuracy,
                    "score": s.score,
                    "range": [s.low, s.high],
                }
                for s in self.sections
            ],
            "best_next_topic": self.best_next_topic,
            "updated_at": self.updated_at,
            "method": self.method,
        }


def accuracy_to_section_score(accuracy: float) -> int:
    accuracy = min(1.0, max(0.0, accuracy))
    return round(SECTION_MIN + SECTION_SPAN * accuracy)


def _confidence(coverage: float, n_reviews: int, avg_ci_width: float) -> str:
    if coverage >= 0.8 and n_reviews >= 1000 and avg_ci_width <= 0.10:
        return "high"
    if coverage >= 0.6 and n_reviews >= 400 and avg_ci_width <= 0.20:
        return "medium"
    return "low"


def project_readiness(
    section_accuracy: dict[str, tuple[float, float, float]],
    n_reviews: int,
    coverage: CoverageReport,
    best_next_topic: str | None = None,
    min_reviews: int = MIN_REVIEWS,
    min_coverage: float = MIN_COVERAGE,
    overconfidence: float = 0.0,
) -> ReadinessReport:
    """Project a readiness score.

    ``section_accuracy`` maps a section id to (point, low, high) accuracy
    estimates from the PERFORMANCE model (not memory). The CI drives the score
    range and the confidence level.

    ``overconfidence`` (0..~0.12) is a calibration penalty: when the student is
    systematically over-confident, the point estimate drops and the lower bound
    drops further (the range widens down), because a self-report of "I know it"
    that keeps being wrong should lower — not raise — a trustworthy projection.
    """
    reasons: list[str] = []
    if n_reviews < min_reviews:
        reasons.append(f"only {n_reviews} graded reviews (need {min_reviews})")
    reasons.extend(coverage.reasons(min_coverage))

    if reasons:
        return ReadinessReport(
            abstained=True,
            reasons=reasons,
            projected_total=None,
            total_low=None,
            total_high=None,
            confidence=None,
            coverage=coverage.weighted_coverage,
            n_reviews=n_reviews,
            sections=[],
            best_next_topic=best_next_topic,
            overconfidence_applied=overconfidence,
        )

    oc = max(0.0, overconfidence)
    sections: list[SectionScore] = []
    ci_widths: list[float] = []
    for sid, (p, lo, hi) in sorted(section_accuracy.items()):
        p_adj = max(0.0, p - 0.5 * oc)
        lo_adj = max(0.0, lo - oc)
        sections.append(
            SectionScore(
                section=sid,
                accuracy=p_adj,
                score=accuracy_to_section_score(p_adj),
                low=accuracy_to_section_score(lo_adj),
                high=accuracy_to_section_score(hi),
            )
        )
        ci_widths.append(max(0.0, hi - lo_adj))

    total = sum(s.score for s in sections)
    total_low = sum(s.low for s in sections)
    total_high = sum(s.high for s in sections)
    avg_ci = sum(ci_widths) / len(ci_widths) if ci_widths else 1.0

    return ReadinessReport(
        abstained=False,
        reasons=[],
        projected_total=total,
        total_low=total_low,
        total_high=total_high,
        confidence=_confidence(coverage.weighted_coverage, n_reviews, avg_ci),
        coverage=coverage.weighted_coverage,
        n_reviews=n_reviews,
        sections=sections,
        best_next_topic=best_next_topic,
        overconfidence_applied=oc,
    )


def section_accuracy_from_topics(
    topic_perf: dict[str, tuple[float, float, float]],
    outline: Outline | None = None,
    uncovered_prior: tuple[float, float, float] = UNCOVERED_PRIOR,
) -> dict[str, tuple[float, float, float]]:
    """Aggregate per-topic performance into per-section accuracy (point, low,
    high), weighted by each topic's in-section exam_weight and adjusted by
    coverage: topics with no performance evidence contribute the uncovered prior,
    so a section that skips heavy topics reads as uncertain and lower (PRD 7.3).
    """
    outline = outline or load_outline()
    out: dict[str, tuple[float, float, float]] = {}
    for section in outline.sections:
        total_w = sum(t.exam_weight for t in section.topic_objs) or 1.0
        p = lo = hi = 0.0
        for t in section.topic_objs:
            w = t.exam_weight / total_w
            tp = topic_perf.get(t.tag)
            pt, plo, phi = tp if tp is not None else uncovered_prior
            p += w * pt
            lo += w * plo
            hi += w * phi
        out[section.id] = (p, lo, hi)
    return out


def readiness_from_topics(
    topic_perf: dict[str, tuple[float, float, float]],
    n_reviews: int,
    coverage: CoverageReport,
    best_next_topic: str | None = None,
    outline: Outline | None = None,
    min_reviews: int = MIN_REVIEWS,
    min_coverage: float = MIN_COVERAGE,
    overconfidence: float = 0.0,
) -> ReadinessReport:
    """PRD 7.3 entry point: project readiness from per-topic performance
    estimates (point, low, high), mapping through exam-weighted, coverage-adjusted
    section accuracies. Delegates the give-up rule + score mapping to
    project_readiness."""
    section_accuracy = section_accuracy_from_topics(topic_perf, outline)
    return project_readiness(
        section_accuracy=section_accuracy,
        n_reviews=n_reviews,
        coverage=coverage,
        best_next_topic=best_next_topic,
        min_reviews=min_reviews,
        min_coverage=min_coverage,
        overconfidence=overconfidence,
    )
