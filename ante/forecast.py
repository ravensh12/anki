# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Trajectory: an exam-date readiness forecast (PRD 7.3 extension).

Anki has no concept of "am I on track for a date". Ante does. Given an exam
date and a daily study budget, this projects:

  * where your readiness score lands ON exam day if you follow the plan, as a
    range (never a promise);
  * the specific unmastered topics that buy the most points per study hour
    ("biggest wins"), by re-running the readiness model with each topic set to
    mastered and measuring the score delta;
  * whether you're on track for a target score, and the daily minutes needed.

It is deliberately built ON TOP of the honest readiness model: the projection
still carries a range, still projects the review count forward (so it is
explicit that the number is conditional on doing the work), and is labelled a
heuristic, not an AAMC concordance. Pure logic; unit-tested without Anki.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import CONFIG, AnteConfig
from .coverage import CoverageReport, compute_coverage
from .mastery import MasteryStatus, TopicMastery
from .outline import Outline, load_outline
from .readiness import (
    accuracy_to_section_score,
    readiness_from_topics,
    section_accuracy_from_topics,
)

DEFAULT_SEC_PER_CARD = 8.0
# rough time to attempt one application/transfer item, minutes
APP_ITEM_MINUTES = 1.5

Perf = dict[str, tuple[float, float, float]]


@dataclass(frozen=True)
class TopicWin:
    tag: str
    name: str
    section_id: str
    points: float  # projected total-score gain if this topic is mastered
    minutes: float  # estimated study minutes to master it
    efficiency: float  # points per study hour
    status: str

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "section_id": self.section_id,
            "points": round(self.points, 1),
            "minutes": round(self.minutes),
            "hours": round(self.minutes / 60.0, 1),
            "efficiency": round(self.efficiency, 2),
            "status": self.status,
        }


@dataclass(frozen=True)
class ForecastReport:
    available: bool
    reason: str | None
    days_remaining: int | None
    daily_minutes: int
    current_total: int | None
    current_range: tuple[int, int] | None
    projected_total: int | None
    projected_range: tuple[int, int] | None
    projected_confidence: str | None
    projected_potential: int | None  # ungated raw projection (coverage-limited)
    blocked_reason: str | None  # why the honest projection abstains, if it does
    topics_masterable: int
    topics_remaining: int
    wins: list[TopicWin]
    target_score: int | None
    on_track: bool | None
    target_gap: int | None
    required_daily_minutes: int | None
    method: str = (
        "Projection: re-runs the readiness model with topics you can realistically "
        "master by your date (in points-at-stake order, within your daily budget, "
        "respecting prerequisites) and projects the review count forward. A range, "
        "conditional on doing the work \u2014 a heuristic, not a guarantee."
    )
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "days_remaining": self.days_remaining,
            "daily_minutes": self.daily_minutes,
            "current_total": self.current_total,
            "current_range": list(self.current_range) if self.current_range else None,
            "projected_total": self.projected_total,
            "projected_range": (
                list(self.projected_range) if self.projected_range else None
            ),
            "projected_confidence": self.projected_confidence,
            "projected_potential": self.projected_potential,
            "blocked_reason": self.blocked_reason,
            "topics_masterable": self.topics_masterable,
            "topics_remaining": self.topics_remaining,
            "wins": [w.as_dict() for w in self.wins],
            "target_score": self.target_score,
            "on_track": self.on_track,
            "target_gap": self.target_gap,
            "required_daily_minutes": self.required_daily_minutes,
            "method": self.method,
            "updated_at": self.updated_at,
        }


def topic_remaining_minutes(
    m: TopicMastery,
    open_items: int,
    sec_per_card: float = DEFAULT_SEC_PER_CARD,
    app_item_minutes: float = APP_ITEM_MINUTES,
) -> float:
    """Estimated minutes to bring a topic to mastery: cards still below strength
    plus application items still to prove."""
    cards_to_strengthen = max(0, m.cards_total - m.cards_at_strength)
    # a card typically needs a couple of successful touches to reach strength
    card_min = cards_to_strengthen * sec_per_card * 2.0 / 60.0
    return card_min + max(0, open_items) * app_item_minutes


def _simulate_mastered(topic_perf: Perf, tags: set[str], cfg: AnteConfig) -> Perf:
    """Return a performance map with ``tags`` raised to (at least) the mastery
    bar, tightly ranged (they are proven)."""
    out: Perf = dict(topic_perf)
    for t in tags:
        cur = out.get(t)
        p = cfg.mastery_bar if cur is None else max(cur[0], cfg.mastery_bar)
        out[t] = (p, p, p)
    return out


def _raw_total(topic_perf: Perf, outline: Outline) -> int:
    """Ungated projected total (no give-up rule) \u2014 used only to *rank* topic
    contributions, so 'biggest wins' always has numbers even before a score is
    honestly shown."""
    sa = section_accuracy_from_topics(topic_perf, outline)
    return sum(accuracy_to_section_score(p) for p, _lo, _hi in sa.values())


def _project(
    topic_perf: Perf,
    projected_reviews: int,
    coverage: CoverageReport,
    outline: Outline,
    cfg: AnteConfig,
) -> tuple[int | None, tuple[int, int] | None, str | None]:
    r = readiness_from_topics(
        topic_perf=topic_perf,
        n_reviews=projected_reviews,
        coverage=coverage,
        outline=outline,
        min_reviews=cfg.giveup_min_reviews,
        min_coverage=cfg.giveup_min_coverage,
    )
    if r.abstained or r.projected_total is None:
        return None, None, r.confidence
    return r.projected_total, (r.total_low or 0, r.total_high or 0), r.confidence


def _masterable_sorted(mastery: dict[str, TopicMastery]) -> list[TopicMastery]:
    """Unmastered topics with cards, ordered by points-at-stake (weight*weakness)."""
    cand = [
        m
        for m in mastery.values()
        if m.status != MasteryStatus.MASTERED and m.cards_total > 0
    ]
    cand.sort(key=lambda m: m.exam_weight * max(m.weakness, 0.01), reverse=True)
    return cand


def _choose_affordable(
    order: list[TopicMastery],
    remaining_work: dict[str, float],
    budget_total: float,
    mastered: set[str],
) -> tuple[list[str], float]:
    """Greedily pick masterable topics in value order, respecting prerequisites
    and the total time budget. Multi-pass so a chosen prereq unlocks dependents."""
    chosen: list[str] = []
    have = set(mastered)
    spent = 0.0
    pool = list(order)
    while True:
        progressed = False
        for m in list(pool):
            if any(p not in have for p in m.prereqs):
                continue
            cost = remaining_work.get(m.tag, 0.0)
            if spent + cost > budget_total:
                pool.remove(m)  # too expensive now and forever this pass-set
                continue
            chosen.append(m.tag)
            have.add(m.tag)
            spent += cost
            pool.remove(m)
            progressed = True
        if not progressed:
            break
    return chosen, spent


def build_forecast(
    mastery: dict[str, TopicMastery],
    topic_perf: Perf,
    coverage: CoverageReport,
    n_reviews: int,
    remaining_work: dict[str, float],
    *,
    days_remaining: int | None,
    daily_minutes: int,
    target_score: int | None = None,
    sec_per_card: float = DEFAULT_SEC_PER_CARD,
    topic_card_counts: dict[str, int] | None = None,
    outline: Outline | None = None,
    cfg: AnteConfig | None = None,
) -> ForecastReport:
    outline = outline or load_outline()
    cfg = cfg or CONFIG
    counts = dict(topic_card_counts or {})
    min_cards = cfg.coverage_min_cards

    def _coverage_covering(extra: set[str]) -> CoverageReport:
        """Coverage assuming the given topics get studied (hence covered) \u2014 the
        forecast is explicitly conditional on doing the work."""
        c = dict(counts)
        for t in extra:
            c[t] = max(c.get(t, 0), min_cards)
        return compute_coverage(c, outline, min_cards)

    mastered_now = {t for t, m in mastery.items() if m.status == MasteryStatus.MASTERED}
    masterable = _masterable_sorted(mastery)

    if days_remaining is None:
        return ForecastReport(
            available=False,
            reason="Set your exam date to see your trajectory.",
            days_remaining=None,
            daily_minutes=daily_minutes,
            current_total=None,
            current_range=None,
            projected_total=None,
            projected_range=None,
            projected_confidence=None,
            projected_potential=None,
            blocked_reason=None,
            topics_masterable=0,
            topics_remaining=len(masterable),
            wins=[],
            target_score=target_score,
            on_track=None,
            target_gap=None,
            required_daily_minutes=None,
        )

    days_remaining = max(0, days_remaining)
    budget_total = days_remaining * daily_minutes
    projected_reviews = n_reviews + int(budget_total * 60 / sec_per_card)

    # current (honest) readiness now
    from .readiness import readiness_from_topics as _rt

    cur = _rt(
        topic_perf=topic_perf,
        n_reviews=n_reviews,
        coverage=coverage,
        outline=outline,
        min_reviews=cfg.giveup_min_reviews,
        min_coverage=cfg.giveup_min_coverage,
    )
    current_total = None if cur.abstained else cur.projected_total
    current_range = (
        None
        if cur.abstained or cur.projected_total is None
        else (cur.total_low or 0, cur.total_high or 0)
    )

    # per-topic contribution ("biggest wins"): ungated raw-score delta from
    # taking each topic to mastery, so the ranking always has numbers.
    raw_base = _raw_total(topic_perf, outline)
    wins: list[TopicWin] = []
    for m in masterable:
        sim = _simulate_mastered(topic_perf, {m.tag}, cfg)
        gain = max(0.0, _raw_total(sim, outline) - raw_base)
        mins = remaining_work.get(m.tag, 0.0)
        eff = gain / (mins / 60.0) if mins > 0 else 0.0
        wins.append(
            TopicWin(
                tag=m.tag,
                name=m.name,
                section_id=m.section_id,
                points=gain,
                minutes=mins,
                efficiency=eff,
                status=m.status.value,
            )
        )
    wins.sort(key=lambda w: (w.points, w.efficiency), reverse=True)

    # what you can realistically master by the date, in value order
    chosen, _spent = _choose_affordable(
        masterable, remaining_work, budget_total, mastered_now
    )
    chosen_set = set(chosen)
    projected_perf = _simulate_mastered(topic_perf, chosen_set, cfg)
    # studying those topics also covers them -> project coverage forward
    projected_coverage = _coverage_covering(chosen_set | mastered_now)
    proj_total, proj_rng, proj_conf = _project(
        projected_perf, projected_reviews, projected_coverage, outline, cfg
    )
    projected_potential = _raw_total(projected_perf, outline)
    blocked_reason = None
    if proj_total is None:
        reasons = projected_coverage.reasons(cfg.giveup_min_coverage)
        if projected_reviews < cfg.giveup_min_reviews:
            reasons.insert(0, f"needs {cfg.giveup_min_reviews} reviews to project")
        blocked_reason = reasons[0] if reasons else None

    # target planning: add topics by efficiency until the target is reached
    on_track: bool | None = None
    target_gap: int | None = None
    required_daily: int | None = None
    if target_score is not None:
        headline = (
            proj_total
            if proj_total is not None
            else _raw_total(projected_perf, outline)
        )
        on_track = headline >= target_score
        target_gap = target_score - headline
        by_eff = sorted(wins, key=lambda w: w.efficiency, reverse=True)
        acc = float(raw_base)
        need_min = 0.0
        have = set(mastered_now)
        for w in by_eff:
            if acc >= target_score:
                break
            if any(p not in have for p in mastery[w.tag].prereqs):
                continue
            acc += w.points
            need_min += w.minutes
            have.add(w.tag)
        if acc >= target_score and days_remaining > 0:
            required_daily = int(round(need_min / days_remaining))

    return ForecastReport(
        available=True,
        reason=None,
        days_remaining=days_remaining,
        daily_minutes=daily_minutes,
        current_total=current_total,
        current_range=current_range,
        projected_total=proj_total,
        projected_range=proj_rng,
        projected_confidence=proj_conf,
        projected_potential=projected_potential,
        blocked_reason=blocked_reason,
        topics_masterable=len(chosen),
        topics_remaining=len(masterable),
        wins=wins[:6],
        target_score=target_score,
        on_track=on_track,
        target_gap=target_gap,
        required_daily_minutes=required_daily,
    )


def days_until(exam_date: str | None, now: float | None = None) -> int | None:
    """Whole days from now until an ISO date (YYYY-MM-DD). None if unparseable."""
    if not exam_date:
        return None
    import datetime

    try:
        y, m, d = (int(x) for x in exam_date.split("-"))
        target = datetime.date(y, m, d)
    except (ValueError, AttributeError):
        return None
    today = (
        datetime.datetime.fromtimestamp(now).date() if now else datetime.date.today()
    )
    return (target - today).days
