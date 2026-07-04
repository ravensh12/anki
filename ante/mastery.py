# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Mastery-gating engine (PRD Section 6) — the core differentiator (Principle 3).

Anki schedules cards; Ante gates *topics*. Each topic moves through four
states driven by demonstrated mastery, not coverage:

    locked  -> prerequisites not yet mastered; cards not introduced
    active  -> unlocked; cards being learned
    corrective -> missed enough items; routed back for focused, re-framed review
                  (Bloom's corrective loop = Leitner demotion at the topic level)
    mastered -> both mastery conditions hold

Mastery condition (PRD 6.1): mastery is gated on demonstrated APPLICATION —
accuracy on quizzes + open-ended items >= MASTERY_BAR (Bloom's bar). It is NOT
gated on flashcard self-ratings; FSRS recall strength is reported separately as a
retention signal. (Set ``mastery_requires_strength`` to also require
cards_at_strength / cards_total >= STRENGTH_FRACTION — the stricter Bloom gate.)

This module is pure logic: it consumes per-topic stats (from the GetTopicMastery
Rust RPC) plus optional per-topic performance accuracy, and returns the gated
state graph that powers the mastery map and the value-ordered recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import CONFIG, AnteConfig
from .outline import Outline, Topic, load_outline


class MasteryStatus(str, Enum):
    LOCKED = "locked"
    ACTIVE = "active"
    CORRECTIVE = "corrective"
    MASTERED = "mastered"


@dataclass(frozen=True)
class TopicStats:
    """Per-topic inputs (cards_at_strength comes from the RPC's mastered_cards
    when queried at R_THRESHOLD)."""

    tag: str
    cards_total: int
    cards_at_strength: int
    average_recall: float
    # performance-model accuracy on this topic's held-out items, if available
    perf_accuracy: float | None = None


@dataclass(frozen=True)
class TopicMastery:
    tag: str
    name: str
    section_id: str
    status: MasteryStatus
    exam_weight: float
    cards_total: int
    cards_at_strength: int
    strength_fraction: float
    average_recall: float
    perf_accuracy: float | None
    # 0..1 blend of strength coverage and application; drives weakness ordering
    normalized_mastery: float
    weakness: float
    prereqs: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()  # unmastered prereqs, if locked
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "section_id": self.section_id,
            "status": self.status.value,
            "exam_weight": self.exam_weight,
            "cards_total": self.cards_total,
            "cards_at_strength": self.cards_at_strength,
            "strength_fraction": round(self.strength_fraction, 4),
            "average_recall": round(self.average_recall, 4),
            "perf_accuracy": (
                round(self.perf_accuracy, 4) if self.perf_accuracy is not None else None
            ),
            "normalized_mastery": round(self.normalized_mastery, 4),
            "weakness": round(self.weakness, 4),
            "prereqs": list(self.prereqs),
            "blocked_by": list(self.blocked_by),
            "reasons": list(self.reasons),
        }


def _normalized_mastery(
    strength_fraction: float, perf_accuracy: float | None, cfg: AnteConfig
) -> float:
    """Blend strength coverage and application into a single 0..1 mastery signal
    (PRD 5.1: weakness = 1 - normalized_mastery). Application (quiz/open-ended)
    dominates the blend, because mastery is proven by USE, not by flashcard
    self-ratings; recall strength is a minor tie-breaker for ordering only."""
    strength_component = min(1.0, strength_fraction / cfg.strength_fraction)
    if perf_accuracy is None:
        # no application evidence yet: strength is a faint hint, not mastery
        return 0.4 * strength_component
    application_component = min(1.0, perf_accuracy / cfg.mastery_bar)
    return 0.75 * application_component + 0.25 * strength_component


def _meets_mastery(
    stats: TopicStats, strength_fraction: float, cfg: AnteConfig
) -> bool:
    """Mastery is gated on demonstrated APPLICATION (quiz + open-ended), never on
    flashcard self-ratings. FSRS card-strength stays a separate retention signal
    unless ``mastery_requires_strength`` is turned on (the stricter Bloom gate)."""
    if stats.perf_accuracy is None:
        # no application/quiz evidence yet -> cannot be mastered (honest)
        return False
    applies = stats.perf_accuracy >= cfg.mastery_bar
    if not applies:
        return False
    if cfg.test_out_enabled:
        return True
    if cfg.mastery_requires_strength:
        return strength_fraction >= cfg.strength_fraction
    # default: application alone masters the topic (proven use, not familiarity)
    return True


def compute_mastery(
    stats_by_tag: dict[str, TopicStats],
    outline: Outline | None = None,
    cfg: AnteConfig | None = None,
) -> dict[str, TopicMastery]:
    """Compute the gated state for every outline topic.

    Two passes: (1) decide which topics meet the mastery condition outright; then
    (2) apply the unlock graph and corrective routing to the rest.
    """
    outline = outline or load_outline()
    cfg = cfg or CONFIG

    topics: list[Topic] = outline.all_topic_objs()

    # pass 1: intrinsic strength + mastery condition
    strength: dict[str, float] = {}
    mastered: set[str] = set()
    for t in topics:
        s = stats_by_tag.get(t.tag)
        if s and s.cards_total > 0:
            frac = s.cards_at_strength / s.cards_total
        else:
            frac = 0.0
        strength[t.tag] = frac
        if s and _meets_mastery(s, frac, cfg):
            mastered.add(t.tag)

    # pass 2: unlock graph + corrective routing
    result: dict[str, TopicMastery] = {}
    for t in topics:
        s = stats_by_tag.get(t.tag)
        cards_total = s.cards_total if s else 0
        cards_at_strength = s.cards_at_strength if s else 0
        avg_recall = s.average_recall if s else 0.0
        perf = s.perf_accuracy if s else None
        frac = strength[t.tag]
        norm = _normalized_mastery(frac, perf, cfg)
        reasons: list[str] = []

        unmastered_prereqs = tuple(p for p in t.prereqs if p not in mastered)

        if t.tag in mastered:
            status = MasteryStatus.MASTERED
            reasons.append(
                f"strength {frac:.0%} and application {(perf or 0):.0%} meet the bar"
            )
        elif unmastered_prereqs:
            status = MasteryStatus.LOCKED
            names = ", ".join(
                (outline.topic(p).name if outline.topic(p) else p)
                for p in unmastered_prereqs
            )
            reasons.append(f"prerequisites not yet mastered: {names}")
        elif perf is not None and perf < cfg.corrective_bar and cards_total > 0:
            status = MasteryStatus.CORRECTIVE
            reasons.append(
                f"application {perf:.0%} dipped below the corrective bar "
                f"{cfg.corrective_bar:.0%}; routed back for re-framed review"
            )
        else:
            status = MasteryStatus.ACTIVE
            if cards_total == 0:
                reasons.append("unlocked; no cards studied yet")
            else:
                need = []
                if frac < cfg.strength_fraction:
                    need.append(
                        f"recall strength {frac:.0%} -> {cfg.strength_fraction:.0%}"
                    )
                if perf is None:
                    need.append("no application evidence yet")
                elif perf < cfg.mastery_bar:
                    need.append(f"application {perf:.0%} -> {cfg.mastery_bar:.0%}")
                if need:
                    reasons.append("to master: " + "; ".join(need))

        # corrective topics get extra weakness so the recommender surfaces them
        weakness = 1.0 - norm
        if status == MasteryStatus.CORRECTIVE:
            weakness = min(1.0, weakness + 0.25)

        result[t.tag] = TopicMastery(
            tag=t.tag,
            name=t.name,
            section_id=t.section_id,
            status=status,
            exam_weight=t.exam_weight,
            cards_total=cards_total,
            cards_at_strength=cards_at_strength,
            strength_fraction=frac,
            average_recall=avg_recall,
            perf_accuracy=perf,
            normalized_mastery=norm,
            weakness=weakness,
            prereqs=t.prereqs,
            blocked_by=unmastered_prereqs,
            reasons=tuple(reasons),
        )
    return result


def next_unlockable(mastery: dict[str, TopicMastery]) -> list[str]:
    """Locked topics whose prerequisites are all mastered would have unlocked, so
    'next unlockable' = locked topics with the FEWEST remaining blockers."""
    locked = [m for m in mastery.values() if m.status == MasteryStatus.LOCKED]
    locked.sort(key=lambda m: (len(m.blocked_by), -m.exam_weight))
    return [m.tag for m in locked]


def mastery_map(
    mastery: dict[str, TopicMastery], outline: Outline | None = None
) -> dict:
    """Per-section rollup for the mastery map UI (PRD 6.4). Reports counts by
    state; never presents coverage as progress."""
    outline = outline or load_outline()
    sections = []
    counts_total = {s.value: 0 for s in MasteryStatus}
    for section in outline.sections:
        counts = {s.value: 0 for s in MasteryStatus}
        topics_out = []
        for t in section.topic_objs:
            m = mastery.get(t.tag)
            if not m:
                continue
            counts[m.status.value] += 1
            counts_total[m.status.value] += 1
            topics_out.append(m.as_dict())
        # progress = genuinely locked in, NOT coverage
        n = len(section.topic_objs)
        sections.append(
            {
                "id": section.id,
                "code": section.code,
                "name": section.name,
                "weight": section.weight,
                "counts": counts,
                "mastered_fraction": counts["mastered"] / n if n else 0.0,
                "topics": topics_out,
            }
        )
    total = sum(counts_total.values()) or 1
    return {
        "sections": sections,
        "counts": counts_total,
        "mastered_fraction": counts_total["mastered"] / total,
    }


def stats_from_mastery_response(
    response, perf_accuracy: dict[str, float] | None = None
) -> dict[str, TopicStats]:
    """Adapt a GetTopicMastery RPC response (queried at R_THRESHOLD so
    mastered_cards == cards_at_strength) into TopicStats keyed by tag."""
    perf_accuracy = perf_accuracy or {}
    out: dict[str, TopicStats] = {}
    for t in response.topics:
        out[t.topic] = TopicStats(
            tag=t.topic,
            cards_total=t.total_cards,
            cards_at_strength=t.mastered_cards,
            average_recall=t.average_recall,
            perf_accuracy=perf_accuracy.get(t.topic),
        )
    return out
