# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Exam-date recalibration engine (Principle 1: WHEN you study is the lever).

Setting a test date is not a cosmetic countdown — in Ante it *recomputes the
whole system*: the daily time budget, the FSRS target (desired retention +
interval cap), the shape of the day (which window gets new vs. review work), and
the reminder cadence. This is the difference between an app that shows a number
and one that changes what you do tomorrow.

The science it encodes:
  * Cepeda 2008 — the optimal spacing gap scales with the retention interval, so
    as the exam approaches we *tighten* reviews (raise desired retention) and cap
    intervals so nothing is scheduled past test day.
  * Bahrick 1993 — wider spacing far out is not laziness, it is efficient; the
    budget can be light when the exam is months away and ramps as it nears.
  * Chronotype/peak-hours — new/hard material goes in the sharp window; light
    spaced review goes off-peak (and right before sleep for consolidation).

Pure logic; unit-tested without Anki. The Qt layer applies ``desired_retention``
and ``max_interval_days`` to the MCAT deck's FSRS config and drives reminders
from ``slot_plan``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import CONFIG, AnteConfig
from .forecast import days_until
from .profile import StudyProfile

# a WORKING day is not a review-every-single-day assumption; students rest.
# We plan against this many effective study days per week for pacing.
STUDY_DAYS_PER_WEEK = 6


@dataclass(frozen=True)
class RecalibrationPlan:
    available: bool
    exam_date: str | None
    days_remaining: int | None
    target_score: int | None
    recommended_daily_minutes: int
    current_daily_minutes: int
    intensity: str
    desired_retention: float
    max_interval_days: int | None
    slot_plan: list[dict]
    pacing: dict
    headline: str
    method: str
    baseline_total: int | None = None
    target_gap: int | None = None

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "exam_date": self.exam_date,
            "days_remaining": self.days_remaining,
            "target_score": self.target_score,
            "recommended_daily_minutes": self.recommended_daily_minutes,
            "current_daily_minutes": self.current_daily_minutes,
            "intensity": self.intensity,
            "desired_retention": self.desired_retention,
            "max_interval_days": self.max_interval_days,
            "slot_plan": self.slot_plan,
            "pacing": self.pacing,
            "headline": self.headline,
            "method": self.method,
            "baseline_total": self.baseline_total,
            "target_gap": self.target_gap,
        }


def desired_retention_for(days_remaining: int | None, cfg: AnteConfig) -> float:
    """Ramp FSRS desired retention from the floor (far out, efficient wide
    spacing) to the ceiling (near test day, tight reviews)."""
    if days_remaining is None:
        return round(cfg.retention_floor, 2)
    if days_remaining <= 0:
        return round(cfg.retention_ceiling, 2)
    if days_remaining >= cfg.retention_ramp_days:
        return round(cfg.retention_floor, 2)
    frac = (cfg.retention_ramp_days - days_remaining) / cfg.retention_ramp_days
    r = cfg.retention_floor + (cfg.retention_ceiling - cfg.retention_floor) * frac
    return round(r, 2)


def _intensity(daily_minutes: int, days_remaining: int | None) -> str:
    if days_remaining is not None and days_remaining <= 7:
        return "crunch"
    if daily_minutes <= 30:
        return "relaxed"
    if daily_minutes <= 90:
        return "steady"
    if daily_minutes <= 165:
        return "intensive"
    return "crunch"


def _slot_roles(windows: list[str], chronotype: str) -> dict[str, tuple[str, str]]:
    """Assign a learning-science role to each chosen window, ordered by
    chronotype so new/hard material lands in the student's sharp window and light
    review lands right before sleep (consolidation)."""
    peak = {
        "lark": "morning",
        "owl": "night",
        "neutral": "during the day",
    }.get(chronotype, "during the day")
    roles: dict[str, tuple[str, str]] = {}
    for w in windows:
        if w == "night":
            # night is always pre-sleep review regardless of chronotype
            roles[w] = ("encode", "light review before sleep — consolidates overnight")
        elif w == peak:
            roles[w] = ("new", "new + hardest material — your sharpest window")
        else:
            roles[w] = ("review", "spaced retrieval of due cards")
    # guarantee the peak window gets 'new' even if it's night for an owl
    if peak == "night" and "night" in roles:
        roles["night"] = ("new", "new + hard material, then a light pre-sleep review")
    return roles


def _slot_plan(profile: StudyProfile, daily_minutes: int) -> list[dict]:
    windows = profile.study_windows or ["morning", "during the day", "night"]
    roles = _slot_roles(windows, profile.chronotype)
    # weight the 'new'/peak window a little heavier; keep night lighter
    weights = {}
    for w in windows:
        kind = roles[w][0]
        weights[w] = 1.3 if kind == "new" else (0.7 if w == "night" else 1.0)
    total_w = sum(weights.values()) or 1.0
    plan: list[dict] = []
    assigned = 0
    for i, w in enumerate(windows):
        if i == len(windows) - 1:
            mins = max(0, daily_minutes - assigned)  # give remainder to last
        else:
            mins = round(daily_minutes * weights[w] / total_w)
            assigned += mins
        kind, detail = roles[w]
        plan.append(
            {
                "window": w,
                "minutes": mins,
                "role": kind,
                "role_detail": detail,
                "peak": kind == "new",
            }
        )
    return plan


def _target_gap_factor(gap: int | None) -> float:
    """Effort multiplier from the diagnostic-baseline → target-score gap.

    Bounded and gentle: each point of climb adds 2% to the recommended budget,
    capped at +40% (a 20-point climb). A gap ≤ 0 (already at/above target)
    leaves the plan untouched — we never manufacture urgency."""
    if gap is None or gap <= 0:
        return 1.0
    return 1.0 + min(20, gap) * 0.02


def recalibrate(
    profile: StudyProfile,
    *,
    due_count: int = 0,
    new_count: int = 0,
    topics_remaining: int = 0,
    remaining_minutes: float = 0.0,
    baseline_total: int | None = None,
    now: float | None = None,
    cfg: AnteConfig | None = None,
) -> RecalibrationPlan:
    """Recompute the whole plan from the exam date + profile + current workload.

    ``remaining_minutes`` is the estimated work to take every still-masterable
    topic to mastery (from ``forecast.topic_remaining_minutes``); ``topics_remaining``
    is how many topics still need mastering. Both are optional so the plan degrades
    gracefully before any content exists.

    ``baseline_total`` is the diagnostic's projected starting score (if taken).
    Together with the target score it sets the *climb* — a bounded effort
    multiplier on the daily budget, so the plan reflects where you start, not
    just when you finish.
    """
    cfg = cfg or CONFIG
    days = days_until(profile.exam_date, now)
    retention = desired_retention_for(days, cfg)
    max_iv = max(1, days) if days is not None else None

    target_gap = (
        int(profile.target_score) - int(baseline_total)
        if profile.target_score is not None and baseline_total is not None
        else None
    )

    # --- recommended daily minutes: spread the remaining work across the days
    # left, floored by a sane daily habit and clamped to the configured band ---
    if days is None:
        recommended = profile.daily_minutes
    else:
        study_days = max(1, math.ceil(days * STUDY_DAYS_PER_WEEK / 7))
        needed_for_mastery = (
            remaining_minutes / study_days if remaining_minutes else 0.0
        )
        # add a light daily review overhead so the plan is honest about upkeep
        review_overhead = min(due_count, 40) * cfg.seconds_per_card / 60.0
        needed = (needed_for_mastery + review_overhead) * _target_gap_factor(target_gap)
        # Honor the student's committed budget as a FLOOR: they told us how much
        # time they'll give, so the plan spends at least that (filling spare
        # capacity with review/ahead) and only recommends MORE when the mastery
        # work genuinely requires it. This keeps the daily target a real study
        # commitment, not a token few minutes on a light day.
        needed = max(needed, float(profile.daily_minutes))
        recommended = int(
            max(
                cfg.min_daily_minutes,
                min(cfg.max_daily_minutes, math.ceil(needed / 5) * 5),
            )
        )
        # never recommend *less* than the habit-forming minimum when time is short
        if days <= 14:
            recommended = max(recommended, 30)

    intensity = _intensity(recommended, days)
    slot_plan = _slot_plan(profile, recommended)

    # --- pacing verdict ---
    if days is None:
        pacing = {
            "available": False,
            "message": "Set your exam date and the whole plan snaps to it.",
        }
        headline = "No exam date yet — add one to recalibrate everything."
    else:
        weeks = max(days / 7.0, 1e-6)
        topics_per_week = (
            round(topics_remaining / weeks, 1) if topics_remaining else 0.0
        )
        deficit = max(0, recommended - profile.daily_minutes)
        on_track = deficit == 0
        if days <= 0:
            msg = "Exam day is here — trust the reps you've banked."
        elif on_track:
            msg = (
                f"On pace: ~{recommended} min/day clears the runway in {days} days "
                f"(you budgeted {profile.daily_minutes})."
            )
        else:
            msg = (
                f"{days} days out you need ~{recommended} min/day — that's "
                f"{deficit} more than your {profile.daily_minutes}-min budget. "
                f"Raise the budget or narrow scope."
            )
        if target_gap is not None and profile.target_score is not None:
            if target_gap > 0:
                msg += (
                    f" The climb: baseline {baseline_total} → target "
                    f"{profile.target_score} (+{target_gap}) is priced into the "
                    "budget."
                )
            else:
                msg += (
                    f" Your baseline ({baseline_total}) already meets your "
                    f"target ({profile.target_score}) — the plan holds the line."
                )
        pacing = {
            "available": True,
            "topics_remaining": topics_remaining,
            "topics_per_week": topics_per_week,
            "on_track": on_track,
            "deficit_minutes": deficit,
            "message": msg,
        }
        countdown = (
            "exam day" if days == 0 else f"{days} day{'s' if days != 1 else ''} out"
        )
        headline = f"{countdown} · ~{recommended} min/day · target retention {int(retention * 100)}%"

    method = (
        "Daily minutes = (remaining mastery work / study-days-left + review "
        "upkeep) x a bounded target-climb factor from the diagnostic baseline, "
        "clamped. Desired retention ramps "
        f"{int(cfg.retention_floor * 100)}%->{int(cfg.retention_ceiling * 100)}% inside "
        f"{cfg.retention_ramp_days} days. Intervals capped at the exam date so no "
        "card is scheduled for after the test."
    )

    return RecalibrationPlan(
        available=days is not None,
        exam_date=profile.exam_date,
        days_remaining=days,
        target_score=profile.target_score,
        recommended_daily_minutes=recommended,
        current_daily_minutes=profile.daily_minutes,
        intensity=intensity,
        desired_retention=retention,
        max_interval_days=max_iv,
        slot_plan=slot_plan,
        pacing=pacing,
        headline=headline,
        method=method,
        baseline_total=baseline_total,
        target_gap=target_gap,
    )
