# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Dashboard assembly + the headline 'time back' planner.

Pure logic (operates on plain dicts) so it is unit-testable without Anki. The Qt
side (qt/aqt/ante.py) converts the GetTopicMastery RPC into the dict shape
this module expects and serves the result over mediasrv.

The three scores are kept separate and honest:
  * Memory  - aggregated FSRS recall over studied cards (+ range).
  * Performance - requires exam-style evaluation data; abstains until present.
  * Readiness - performance -> MCAT score, gated by the give-up rule.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Iterable

from .analytics import timing_summary
from .circuit import build_world
from .comprehension import build_comprehension
from .config import CONFIG, AnteConfig
from .coverage import compute_coverage, topic_counts_from_mastery  # noqa: F401
from .diagnosis import diagnose
from .diagnostic import summarize_diagnostic
from .documentary import build_documentary
from .dreamseed import build_reel
from .forecast import build_forecast, days_until, topic_remaining_minutes
from .intentions import default_intentions
from .mastery import (
    MasteryStatus,
    TopicStats,
    compute_mastery,
    mastery_map,
    next_unlockable,
)
from .memory import wilson_interval
from .metacognition import (
    calibration_comparison,
    calibration_report,
    combined_calibration,
    flashcard_calibration,
    overconfidence_penalty,
    self_trust,
)
from .outline import load_outline
from .performance_items import due_items
from .profile import StudyProfile
from .readiness import project_readiness, readiness_from_topics
from .trackrecord import evaluate as evaluate_track_record
from .recalibrate import recalibrate
from .reminders import build_schedule, next_reminder, what_to_do_now
from .rewards import (
    compute_streak,
    consistency_status,
    mastery_milestone_reward,
    mastery_momentum,
    motivation_state,
)
from .rhythm import peak_windows
from .ritual import bookends, night_shift
from .sessions import plan_micro_session
from .studyplan import build_study_plan, marked_nights

# default daily plan: 15 min night + 30 morning + 30 day = 75 min.
DEFAULT_SLOTS = [("morning", 30), ("during the day", 30), ("night", 15)]
DEFAULT_SEC_PER_CARD = 8.0


def _memory_score(topics: list[dict]) -> dict:
    """Aggregate recall over studied cards, with a confidence range."""
    studied = sum(t["studied_cards"] for t in topics)
    if studied == 0:
        return {"available": False, "reason": "no cards studied yet"}
    weighted_recall = (
        sum(t["average_recall"] * t["studied_cards"] for t in topics) / studied
    )
    # treat aggregate recall as a proportion to get an honest range
    successes = round(weighted_recall * studied)
    lo, hi = wilson_interval(successes, studied)
    return {
        "available": True,
        "recall": round(weighted_recall, 4),
        "range": [round(lo, 4), round(hi, 4)],
        "n_studied": studied,
    }


def best_next_topic(topics: list[dict]) -> str | None:
    """Highest points-at-stake: weight * weakness (1 - average_recall)."""
    candidates = [t for t in topics if t["total_cards"] > 0]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda t: t["weight"] * (1.0 - t["average_recall"]),
    )["topic"]


def time_back_plan(
    due_count: int,
    new_count: int,
    budget_minutes: int,
    sec_per_card: float = DEFAULT_SEC_PER_CARD,
    slots: list[tuple[str, int]] | None = None,
) -> dict:
    """The 'time back' planner: what a fixed daily budget buys you.

    The pitch: give the schedule ~budget focused minutes a day and the
    points-at-stake order spends them on the highest-value cards first - no
    all-nighters.
    """
    slots = slots or DEFAULT_SLOTS
    slot_total = sum(m for _, m in slots) or 1
    # scale the default slot shape to the requested budget
    scaled = [(name, round(budget_minutes * mins / slot_total)) for name, mins in slots]

    capacity = int(budget_minutes * 60 / sec_per_card)
    workload = due_count  # new cards are optional headroom
    due_minutes = math.ceil(workload * sec_per_card / 60)

    # allocate today's capacity across slots, due cards first
    remaining = min(capacity, workload)
    slot_plan = []
    for name, mins in scaled:
        slot_cap = int(mins * 60 / sec_per_card)
        take = min(slot_cap, remaining)
        remaining -= take
        slot_plan.append({"slot": name, "minutes": mins, "cards": take})

    covers = capacity >= workload
    if covers:
        spare = capacity - workload
        message = (
            f"{budget_minutes} min/day covers today's {workload} due cards "
            f"with room for ~{spare} more - no marathon needed."
        )
    else:
        message = (
            f"Today's {workload} due cards need ~{due_minutes} min; your "
            f"{budget_minutes} min handles the {capacity} highest-value ones "
            f"first (points-at-stake order). Add ~{due_minutes - budget_minutes} "
            f"min or trust the order to spend time where it counts."
        )

    return {
        "budget_minutes": budget_minutes,
        "sec_per_card": sec_per_card,
        "daily_capacity_cards": capacity,
        "due_count": due_count,
        "new_count": new_count,
        "due_minutes_needed": due_minutes,
        "covers_due_load": covers,
        "slots": slot_plan,
        "message": message,
    }


def _stats_from_topic_dicts(
    topics: list[dict], topic_performance: dict[str, tuple[float, float, float]] | None
) -> dict[str, TopicStats]:
    """Build mastery inputs from dashboard topic dicts. ``mastered_cards`` is the
    count at FSRS strength (RPC queried at R_THRESHOLD), i.e. cards_at_strength."""
    perf_point = (
        {k: v[0] for k, v in topic_performance.items()} if topic_performance else {}
    )
    out: dict[str, TopicStats] = {}
    for t in topics:
        out[t["topic"]] = TopicStats(
            tag=t["topic"],
            cards_total=t["total_cards"],
            cards_at_strength=t.get("mastered_cards", 0),
            average_recall=t["average_recall"],
            perf_accuracy=perf_point.get(t["topic"]),
        )
    return out


def _paraphrase_gaps(
    topics: list[dict],
    topic_performance: dict[str, tuple[float, float, float]] | None,
) -> list[dict]:
    """Per-topic memory-minus-application gap for the Diagnosis (PRD 7d)."""
    recall_by = {t["topic"]: t["average_recall"] for t in topics}
    gaps: list[dict] = []
    for tag, (p, _lo, _hi) in (topic_performance or {}).items():
        r = recall_by.get(tag)
        if r is None:
            continue
        gaps.append(
            {
                "topic": tag,
                "recall": round(r, 3),
                "application": round(p, 3),
                "gap": round(r - p, 3),
            }
        )
    return gaps


def _mastery_aware_next_topic(mastery: dict) -> str | None:
    """Highest points-at-stake among study-able (active/corrective) topics; a
    corrective topic outranks an equally-weak active one (boosted weakness)."""
    candidates = [
        m
        for m in mastery.values()
        if m.status in (MasteryStatus.ACTIVE, MasteryStatus.CORRECTIVE)
        and m.cards_total > 0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda m: m.exam_weight * m.weakness).tag


def build_dashboard(  # noqa: PLR0913
    topics: Iterable[dict],
    *,
    due_count: int,
    new_count: int,
    n_reviews: int,
    budget_minutes: int = 75,
    sec_per_card: float = DEFAULT_SEC_PER_CARD,
    section_performance: dict[str, tuple[float, float, float]] | None = None,
    topic_performance: dict[str, tuple[float, float, float]] | None = None,
    newly_mastered_count: int = 0,
    active_days: int = 0,
    genuine_by_day: dict[int, int] | None = None,
    today_ordinal: int = 0,
    exam_date: str | None = None,
    target_score: int | None = None,
    quiz_responses: Mapping[str, object] | None = None,
    open_responses: Mapping[str, object] | None = None,
    flash_confidence: list | None = None,
    hour_outcomes: list[tuple[int, int]] | None = None,
    timing_events: list[tuple[bool, int | None]] | None = None,
    profile: Mapping[str, object] | None = None,
    now_hour: int | None = None,
    hour_counts_today: dict[int, int] | None = None,
    diagnostic: Mapping[str, object] | None = None,
    palace_index: Mapping[int, Mapping] | None = None,
    palace_by_topic: Mapping[str, int] | None = None,
    palace_total: int = 0,
    viva_log: list[dict] | None = None,
    events_today: Sequence[Mapping] | None = None,
    studio_status: Mapping[str, object] | None = None,
    overnight: tuple[int, int] | None = None,
    readiness_history: list[dict] | None = None,
    fl_results: Mapping[str, object] | None = None,
    now: object | None = None,
    outline=None,
    cfg: AnteConfig | None = None,
) -> dict:
    topics = [dict(t) for t in topics]
    outline = outline or load_outline()
    cfg = cfg or CONFIG

    # ``now`` overrides "today" for the date math (exam countdown, phase, calendar,
    # retention ramp). Demo mode uses it to time-travel; production leaves it None.
    import datetime as _dt

    now_date = now if isinstance(now, _dt.date) else None
    now_ts = (
        _dt.datetime(now_date.year, now_date.month, now_date.day, 12).timestamp()
        if now_date is not None
        else None
    )

    # Personalization profile: use the passed profile, else synthesize one from
    # the legacy exam_date/target/budget args so older callers still work.
    prof = (
        StudyProfile.from_dict(dict(profile), cfg)
        if profile is not None
        else StudyProfile(
            exam_date=exam_date,
            target_score=target_score,
            daily_minutes=budget_minutes,
        )
    )
    exam_date = prof.exam_date
    target_score = prof.target_score
    budget_minutes = prof.daily_minutes
    clock_hour = now_hour if now_hour is not None else 12
    counts = {t["topic"]: t["total_cards"] for t in topics}
    coverage = compute_coverage(counts, outline)

    memory = _memory_score(topics)

    # Mastery-gating: the four-state map (Principle 3). Topics need application
    # evidence (topic_performance) to reach 'mastered'; honest otherwise.
    stats = _stats_from_topic_dicts(topics, topic_performance)
    mastery = compute_mastery(stats, outline, cfg)
    m_map = mastery_map(mastery, outline)
    next_topic = _mastery_aware_next_topic(mastery) or best_next_topic(topics)

    # Confidence calibration, measured separately for FLASHCARDS (did "I know
    # this" before flipping match actual recall?) and the QUIZ (did "I'm sure"
    # match applying it on a new question?), plus a combined self-trust signal.
    # Overconfidence in EITHER lowers AND widens the honest readiness range:
    # saying "sure" and being wrong pushes the interval DOWN, because a
    # self-report that keeps missing can't be trusted up.
    flash_cal = flashcard_calibration(flash_confidence or [])
    quiz_cal = calibration_report(
        quiz_responses or {}, open_responses=open_responses or {}
    )
    calibration = combined_calibration(
        quiz_responses or {}, open_responses or {}, flash_confidence or []
    )
    calibration_sources = {
        "flashcard": flash_cal,
        "application": quiz_cal,
        "combined": calibration,
        "comparison": calibration_comparison(flash_cal, quiz_cal),
    }
    overconfidence = overconfidence_penalty(calibration, cfg)

    # Performance: prefer per-topic evidence; fall back to legacy section input.
    if topic_performance:
        performance = {
            "available": True,
            "topics": {
                k: {"accuracy": v[0], "range": [v[1], v[2]]}
                for k, v in topic_performance.items()
            },
        }
        readiness = readiness_from_topics(
            topic_perf=topic_performance,
            n_reviews=n_reviews,
            coverage=coverage,
            best_next_topic=next_topic,
            outline=outline,
            min_reviews=cfg.giveup_min_reviews,
            min_coverage=cfg.giveup_min_coverage,
            overconfidence=overconfidence,
        ).as_dict()
    elif section_performance:
        performance = {
            "available": True,
            "sections": {
                sid: {"accuracy": p, "range": [lo, hi]}
                for sid, (p, lo, hi) in section_performance.items()
            },
        }
        readiness = project_readiness(
            section_accuracy=section_performance,
            n_reviews=n_reviews,
            coverage=coverage,
            best_next_topic=next_topic,
            min_reviews=cfg.giveup_min_reviews,
            min_coverage=cfg.giveup_min_coverage,
            overconfidence=overconfidence,
        ).as_dict()
    else:
        performance = {
            "available": False,
            "reason": (
                "no exam-style performance evaluation yet - memory alone cannot "
                "predict passage performance"
            ),
        }
        readiness = project_readiness(
            section_accuracy={},
            n_reviews=n_reviews,
            coverage=coverage,
            best_next_topic=next_topic,
            min_reviews=cfg.giveup_min_reviews,
            min_coverage=cfg.giveup_min_coverage,
            overconfidence=overconfidence,
        ).as_dict()
        if "no performance" not in " ".join(readiness["reasons"]):
            readiness["reasons"].insert(0, "no performance evaluation available yet")
            readiness["abstained"] = True
            readiness["projected_total"] = None
            readiness["total_range"] = None

    # How accurate the Book's PAST lines turned out to be, checked against real
    # completed full-lengths (spec section 1 honesty rule). Abstains until at
    # least one past line has an actual score to check it against.
    readiness["track_record"] = evaluate_track_record(
        list(readiness_history or []), dict(fl_results or {})
    ).as_dict()

    # --- Predictive-coach layer (Trajectory / Calibration / Peak Hours / Diagnosis) ---
    gaps = _paraphrase_gaps(topics, topic_performance)
    rhythm = peak_windows(hour_outcomes or [])

    open_by_topic: dict[str, int] = {}
    for it in due_items(quiz_responses or {}):
        open_by_topic[it.topic] = open_by_topic.get(it.topic, 0) + 1
    remaining_work = {
        tag: topic_remaining_minutes(m, open_by_topic.get(tag, 0), sec_per_card)
        for tag, m in mastery.items()
    }
    forecast = build_forecast(
        mastery,
        topic_performance or {},
        coverage,
        n_reviews,
        remaining_work,
        days_remaining=days_until(exam_date, now_ts),
        daily_minutes=budget_minutes,
        target_score=target_score,
        sec_per_card=sec_per_card,
        topic_card_counts=counts,
        outline=outline,
        cfg=cfg,
    ).as_dict()
    diagnosis = diagnose(mastery, gaps, calibration, coverage, rhythm)

    # --- Complete comprehension (Atlas), exam-date recalibration, reminders,
    # response-time analytics, and the composed motivation surface ---
    comprehension = build_comprehension(
        mastery, topic_performance or {}, calibration, outline, cfg
    )
    topics_remaining = sum(
        1
        for m in mastery.values()
        if m.status != MasteryStatus.MASTERED and m.cards_total > 0
    )
    remaining_minutes_total = sum(remaining_work.values())

    # The Baseline Diagnostic (if taken): its summary feeds the plan's climb
    # and is surfaced alongside readiness as "where you started".
    diag = dict(diagnostic) if diagnostic else {}
    diag_summary = None
    diag_item_ids = diag.get("item_ids")
    if diag_item_ids:
        diag_summary = summarize_diagnostic(
            list(diag_item_ids),  # type: ignore[call-overload]
            quiz_responses or {},
            open_responses or {},
            outline=outline,
            cfg=cfg,
        )
    diagnostic_payload = {
        "taken": bool(diag.get("taken_at")),
        "skipped": bool(diag.get("skipped")),
        "taken_at": diag.get("taken_at"),
        "summary": diag_summary,
    }
    baseline_total = diag_summary.get("baseline_total") if diag_summary else None

    recal = recalibrate(
        prof,
        due_count=due_count,
        new_count=new_count,
        topics_remaining=topics_remaining,
        remaining_minutes=remaining_minutes_total,
        baseline_total=baseline_total,
        now=now_ts,
        cfg=cfg,
    )
    # the next marked night (quiz checkpoint / full-length) becomes a dated
    # reminder, so test nights announce themselves instead of hiding in the
    # calendar. Soonest first; offset 0 means tonight.
    upcoming_marked = marked_nights(recal.days_remaining, today=now_date)
    schedule = build_schedule(
        prof,
        recal.slot_plan,
        due_count=due_count,
        best_next_topic=next_topic,
        days_remaining=recal.days_remaining,
        sec_per_card=sec_per_card,
        marked_night=upcoming_marked[0] if upcoming_marked else None,
        cfg=cfg,
    )
    today_iso = (now_date or _dt.date.today()).isoformat()
    nxt = next_reminder(schedule, clock_hour, today=today_iso)
    reminders = {
        "enabled": prof.reminders_enabled,
        "schedule": [r.as_dict() for r in schedule],
        "next": nxt.as_dict() if nxt else None,
        "now": what_to_do_now(
            due_count=due_count,
            best_next_topic=next_topic,
            recommended_daily_minutes=recal.recommended_daily_minutes,
            now_hour=clock_hour,
            sec_per_card=sec_per_card,
        ),
    }
    ritual = bookends(
        hour_counts_today or {},
        schedule=[r.as_dict() for r in schedule],
        now_hour=clock_hour,
    )
    # The consolidation night: (settled, loose) counts from yesterday's play,
    # narrated at the morning game. Purely informational; honest counts only.
    settled, loose = overnight if overnight else (0, 0)
    night_report = night_shift(settled, loose, now_hour=clock_hour)

    # The done-for-you plan + calendar: turn the recalibration dials into a
    # day-by-day schedule the student can just follow (Principle 2).
    study_plan = build_study_plan(
        mastery,
        days_remaining=recal.days_remaining,
        daily_minutes=recal.recommended_daily_minutes,
        exam_date=exam_date,
        sec_per_card=sec_per_card,
        baseline_total=baseline_total,
        target_score=target_score,
        outline=outline,
        cfg=cfg,
        slot_plan=recal.slot_plan,
        now=now_date,
    )
    # --- The generative-media layer + the world (Mnemopolis) ---
    # Vivas: which topics can be defended out loud to seal mastery.
    from .viva import eligible_topics

    viva_suggested = eligible_topics(mastery, open_responses or {}, cfg)

    # Dream Seed: tonight's Last Light reel from today's genuine study events.
    dreamseed = build_reel(
        list(events_today or []),
        palace_by_card=palace_index or {},
        now_hour=clock_hour,
        cfg=cfg,
    )

    # The Documentary: the exam-eve montage (available only near the exam).
    documentary = build_documentary(
        exam_days_left=days_until(exam_date, now_ts),
        diagnostic=diagnostic_payload,
        readiness=readiness,
        streak=compute_streak(genuine_by_day or {}, today_ordinal, cfg).as_dict(),
        n_reviews=n_reviews,
        active_days=active_days,
        topics_mastered=m_map["counts"]["mastered"],
        viva_passed=sum(1 for s in (viva_log or []) if s.get("status") == "passed"),
        palace_records=list((palace_index or {}).values()),
        baseline_total=baseline_total,
    )

    # due-by-topic for the city's building badges + the waypoint's target.
    due_by_topic: dict[str, int] = dict(open_by_topic)
    for m in mastery.values():
        if m.status in (MasteryStatus.ACTIVE, MasteryStatus.CORRECTIVE):
            due_by_topic.setdefault(m.tag, 0)
            due_by_topic[m.tag] += max(0, m.cards_total - m.cards_at_strength)

    world = build_world(
        mastery,
        ritual=ritual,
        readiness=readiness,
        due_count=due_count,
        new_count=new_count,
        due_by_topic=due_by_topic,
        best_next_topic=next_topic,
        diagnostic_taken=bool(diagnostic_payload.get("taken")),
        palace_counts=palace_by_topic or {},
        palace_total=palace_total,
        viva_suggested=viva_suggested,
        dreamseed_ready=bool(dreamseed.get("available")),
        documentary_ready=bool(documentary.get("available")),
        exam_days_left=days_until(exam_date, now_ts),
        now_hour=clock_hour,
        outline=outline,
    )

    timing = timing_summary(timing_events or [], cfg)
    motivation = motivation_state(
        newly_mastered_count=newly_mastered_count,
        genuine_by_day=genuine_by_day or {},
        today_ordinal=today_ordinal,
        active_days=active_days,
        topics_mastered_total=m_map["counts"]["mastered"],
        opt_in=prof.rewards_opt_in,
        cfg=cfg,
    )
    trust = self_trust(calibration)

    # sort topics for display: highest points-at-stake first (mastery weakness)
    topics_sorted = sorted(
        topics,
        key=lambda t: mastery[t["topic"]].exam_weight * mastery[t["topic"]].weakness
        if t["topic"] in mastery
        else t["weight"] * (1.0 - t["average_recall"]),
        reverse=True,
    )

    return {
        "exam": outline.exam,
        "product": "Ante",
        "world": world,
        "viva": {"suggested": viva_suggested},
        "dreamseed": dreamseed,
        "documentary": documentary,
        "studio": dict(studio_status)
        if studio_status
        else {"providers": {}, "assets": {}},
        "scores": {
            "memory": memory,
            "performance": performance,
            "readiness": readiness,
        },
        "mastery_map": m_map,
        "next_unlockable": next_unlockable(mastery)[:5],
        "coverage": {
            "overall": round(coverage.overall_coverage, 4),
            "weighted": round(coverage.weighted_coverage, 4),
            "covered_topics": coverage.covered_topics,
            "total_topics": coverage.total_topics,
            "abstains": coverage.abstains(),
            "missing_high_weight_sections": coverage.missing_high_weight_sections,
            "sections": [
                {
                    "id": s.id,
                    "name": s.name,
                    "weight": s.weight,
                    "covered": s.covered,
                    "total": s.total,
                    "fraction": round(s.fraction, 4),
                }
                for s in coverage.sections
            ],
        },
        "best_next_topic": next_topic,
        "comprehension": comprehension,
        "recalibration": recal.as_dict(),
        "study_plan": study_plan,
        "reminders": reminders,
        "ritual": ritual,
        "night_shift": night_report,
        "diagnostic": diagnostic_payload,
        "timing": timing,
        "self_trust": trust,
        "forecast": forecast,
        "calibration": calibration,
        "calibration_sources": calibration_sources,
        "rhythm": rhythm,
        "diagnosis": diagnosis,
        "exam_date": exam_date,
        "target_score": target_score,
        "profile": prof.as_dict(),
        "time_back": time_back_plan(due_count, new_count, budget_minutes, sec_per_card),
        "micro_session": plan_micro_session(due_count, cfg=cfg).as_dict(),
        "motivation": motivation,
        "momentum": mastery_momentum(newly_mastered_count).as_dict(),
        "consistency": consistency_status(active_days).as_dict(),
        "streak": compute_streak(genuine_by_day or {}, today_ordinal, cfg).as_dict(),
        "milestone": mastery_milestone_reward(m_map["counts"]["mastered"]).as_dict(),
        "intentions": [i.as_dict() for i in default_intentions(cfg)],
        "topics": topics_sorted,
    }
