# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Demo mode — a fully-populated, time-travellable Ante instrument.

Builds the entire dashboard from a *synthetic but coherent* study history that
is a function of a single knob: ``day`` (0..RUNWAY). Skip the day forward and
everything moves the way it would in real life — the exam countdown shrinks, the
phase advances Build -> Bridge -> Sharpen, FSRS retention ramps, the mastery map
greens, the readiness score climbs out of abstention, the streak grows, and the
calendar re-anchors. Flags flip qualitative states (overconfident vs calibrated,
rewards on/off) so every surface can be shown on demand.

Nothing here touches the real collection — it's a self-contained dataset fed
through the same ``build_dashboard`` engine the real app uses, so what you see is
the genuine logic, just on simulated inputs.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from .app import build_dashboard
from .applied import combined_topic_performance
from .config import CONFIG, AnteConfig
from .diagnostic import build_diagnostic
from .fulllength import SECTION_NAMES, SECTION_ORDER
from .openended import load_open_items
from .outline import load_outline
from .performance_items import load_items

RUNWAY = 90  # a 90-day plan
_SECOND_PER_DAY = 86400

# per-section skill ceiling — bio strongest, CARS the grind (shows a gradient)
_SECTION_CEIL = {
    "bio_biochem": 0.92,
    "chem_phys": 0.82,
    "psych_soc": 0.74,
    "cars": 0.64,
}
_CARDS_PER_TOPIC = 8


def _progress(day: int) -> float:
    """0..1 mastery progress; most learning banked by ~day 65, then upkeep."""
    return max(0.0, min(1.0, day / 65.0))


def _jitter(seed: str) -> float:
    return (hash(seed) % 1000) / 1000.0  # deterministic 0..1


def _topic_accuracy(tag: str, section: str, p: float) -> float:
    ceil = _SECTION_CEIL.get(section, 0.7)
    base = 0.30 + 0.70 * p
    j = (_jitter(tag) - 0.5) * 0.18
    return max(0.05, min(0.99, ceil * base + j))


def build_demo_dashboard(
    day: int = 12,
    flags: dict | None = None,
    cfg: AnteConfig | None = None,
    today: date | None = None,
) -> dict:
    """The full dashboard payload for demo ``day`` (0..RUNWAY)."""
    cfg = cfg or CONFIG
    flags = flags or {}
    day = max(0, min(RUNWAY, int(day)))
    # the simulator's clock: lets every hour-dependent surface (bookends,
    # the night phase, the last-hand reel, the night-shift report) be demoed
    hour = max(0, min(23, int(flags.get("hour", 9))))
    today = today or date.today()
    rng = random.Random(1000 + day)

    # The story ARC (so time-travel shows different diagnoses, like a real
    # course): early = big recall-vs-application gap + overconfidence; mid =
    # the gap closes but confidence still runs hot; late = calibrated, no leak.
    overconfident = day < 55
    rewards = True

    p = _progress(day)
    days_remaining = RUNWAY - day
    exam_date = (today + timedelta(days=days_remaining)).isoformat()

    outline = load_outline()

    # --- per-topic stats (drives mastery map, coverage, memory) ---
    # Coverage GROWS with the plan: you add/reach cards for more topics over time,
    # so early days are sparsely covered (readiness abstains) and it fills in.
    all_topics = outline.all_topic_objs()
    # interleave sections (like the real plan's rotating focus) so every section
    # gains coverage early — highest-weight topic of each section first, etc.
    by_sec: dict[str, list] = {}
    for t in sorted(all_topics, key=lambda t: outline.topic_weight(t.tag), reverse=True):
        by_sec.setdefault(t.section_id, []).append(t)
    sec_order = sorted(by_sec, key=lambda s: outline.topic_weight(by_sec[s][0].tag), reverse=True)
    ordered = []
    while any(by_sec.values()):
        for sec in sec_order:
            if by_sec[sec]:
                ordered.append(by_sec[sec].pop(0))
    covered_n = max(2, round(len(ordered) * min(1.0, day / 50.0)))
    covered = {t.tag for t in ordered[:covered_n]}
    topics: list[dict] = []
    acc_by_topic: dict[str, float] = {}
    for t in all_topics:
        if t.tag not in covered:
            # not yet reached — no cards, so it reads as uncovered on the map
            topics.append(
                {
                    "topic": t.tag,
                    "weight": outline.topic_weight(t.tag),
                    "total_cards": 0,
                    "studied_cards": 0,
                    "mastered_cards": 0,
                    "average_recall": 0.0,
                    "coverage": 0.0,
                }
            )
            continue
        acc = _topic_accuracy(t.tag, t.section_id, p)
        acc_by_topic[t.tag] = acc
        studied = round(_CARDS_PER_TOPIC * min(1.0, 0.25 + p))
        mastered = round(studied * acc)
        # recall runs AHEAD of application early (the familiarity trap); the gap
        # closes — and flips slightly negative — as the plan forces transfer
        # practice, so the Diagnosis verdict CHANGES across the course:
        # early = transfer gap, mid = overconfidence, late = clear.
        gap = (0.32 - 0.45 * p) * (0.7 + 0.6 * _jitter(t.tag + "gap"))
        recall = max(0.10, min(0.97, acc + gap))
        topics.append(
            {
                "topic": t.tag,
                "weight": outline.topic_weight(t.tag),
                "total_cards": _CARDS_PER_TOPIC,
                "studied_cards": studied,
                "mastered_cards": mastered,
                "average_recall": round(recall, 3),
                "coverage": round(min(1.0, 0.25 + p), 3),
            }
        )

    # --- synthetic answered items (drives calibration, readiness, quiz_status) ---
    import time as _time

    now_ts = _time.time()
    quiz_responses: dict[str, list] = {}
    open_responses: dict[str, list] = {}
    flash_confidence: list = []
    # answer a growing fraction of the banks as the plan progresses
    answered_frac = min(1.0, 0.2 + p)
    for it in load_items():
        if it.topic not in acc_by_topic:
            continue  # only covered topics have been studied
        if rng.random() > answered_frac:
            continue
        acc = acc_by_topic[it.topic]
        correct = rng.random() < acc
        choice = it.correct_index if correct else (it.correct_index + 1) % 4
        conf = _demo_conf(correct, overconfident, rng)
        ts = now_ts - rng.randint(0, min(day, 20)) * _SECOND_PER_DAY
        quiz_responses[it.id] = [[choice, ts, conf, rng.randint(4000, 16000)]]
    for oit in load_open_items():
        if oit.topic not in acc_by_topic:
            continue
        if rng.random() > answered_frac:
            continue
        acc = acc_by_topic[oit.topic]
        score = round(max(0.0, min(1.0, acc + rng.uniform(-0.15, 0.15))), 2)
        conf = _demo_conf(score >= cfg.open_pass_score, overconfident, rng)
        ts = now_ts - rng.randint(0, min(day, 20)) * _SECOND_PER_DAY
        open_responses[oit.id] = [[score, ts, conf, rng.randint(9000, 26000)]]
    # flashcard confidence log (familiarity trap): overconfident feels sure, misses
    for _ in range(min(200, day * 6)):
        felt_correct = rng.random() < (0.5 + 0.4 * p)
        conf = _demo_conf(felt_correct, overconfident, rng)
        flash_confidence.append(
            [round(conf, 2), 1 if felt_correct else 0, now_ts, "", rng.randint(2000, 12000)]
        )

    topic_perf = combined_topic_performance(quiz_responses, open_responses)

    # synthetic today's events (Dream Seed reel) + a small commissioned palace
    events_today = _demo_events_today(topics, acc_by_topic, rng)
    demo_palace = _demo_palace(acc_by_topic, day)
    palace_index = {int(r["card_id"]): r for r in demo_palace}
    palace_by_topic: dict[str, int] = {}
    for r in demo_palace:
        palace_by_topic[r["topic"]] = palace_by_topic.get(r["topic"], 0) + 1

    # --- streak / consistency / ritual inputs ---
    today_ordinal = int(now_ts // _SECOND_PER_DAY)
    streak_days = min(day, 34)
    genuine_by_day = {
        today_ordinal - k: rng.randint(18, 45) for k in range(streak_days)
    }
    active_days = min(7, streak_days)
    # bookends follow the simulated clock: the morning game reads as *banked*
    # only once its window has passed (midday+), and the midnight game is left
    # unbanked through dusk/night so the tour can actually play it.
    hour_counts_today = {}
    if day > 0 and hour >= 12:
        hour_counts_today[8] = 14
    n_reviews = day * 45
    newly_mastered = sum(
        1 for t in topics if t["mastered_cards"] / max(1, t["total_cards"]) >= 0.8
    )

    # --- diagnostic: taken on day 0, baseline from those answers ---
    form = build_diagnostic()
    diagnostic = {
        "taken_at": now_ts - day * _SECOND_PER_DAY,
        "skipped": False,
        "item_ids": form.item_ids,
    }

    profile = {
        "exam_date": exam_date,
        "target_score": 515,
        "daily_minutes": 120,
        "chronotype": "lark",
        "reminders_enabled": True,
        "background_reminders": False,
        "rewards_opt_in": rewards,
        "onboarded": True,
    }

    payload = build_dashboard(
        topics,
        due_count=max(0, 60 - day),
        new_count=max(0, 30 - day // 2),
        n_reviews=n_reviews,
        budget_minutes=120,
        active_days=active_days,
        topic_performance=topic_perf or None,
        genuine_by_day=genuine_by_day,
        today_ordinal=today_ordinal,
        newly_mastered_count=newly_mastered,
        quiz_responses=quiz_responses,
        open_responses=open_responses,
        flash_confidence=flash_confidence,
        hour_outcomes=[(8, 1), (8, 1), (21, 1), (14, 0)] * max(1, day),
        timing_events=[(True, 6000), (False, 2500), (True, 9000)] * max(1, day),
        profile=profile,
        now_hour=hour,
        hour_counts_today=hour_counts_today,
        overnight=(
            (max(6, min(60, day * 2)), max(0, 60 - day)) if day > 0 else (0, 0)
        ),
        diagnostic=diagnostic,
        palace_index=palace_index,
        palace_by_topic=palace_by_topic,
        palace_total=len(demo_palace),
        events_today=events_today,
        now=today,
        outline=outline,
        cfg=cfg,
    )
    # demo control metadata for the UI bar
    payload["demo"] = {
        "enabled": True,
        "day": day,
        "hour": hour,
        "runway": RUNWAY,
        "days_remaining": days_remaining,
        "phase": (payload.get("study_plan") or {}).get("today", {}).get("phase"),
        "jumps": _demo_jumps(),
    }
    # quiz_status for the calendar/plan (mirror the Qt helper, on synthetic data)
    payload["quiz_status"] = _demo_quiz_status(quiz_responses, open_responses)
    payload["fl_results"] = _demo_fl_results(day, days_remaining, now_ts)
    payload["film_clips"] = {}
    payload["studio"] = {
        "providers": {"offline_only": True},
        "budget": {"allowed": True, "daily_used": 0, "daily_cap": cfg.studio_daily_cap},
        "assets": {"still": len(demo_palace), "motion": 0},
    }
    payload["palace_gallery"] = _demo_palace_gallery(demo_palace)
    return payload


def _demo_events_today(topics: list[dict], acc_by_topic: dict[str, float], rng) -> list[dict]:
    """A handful of today's genuine study events for the Dream Seed reel."""
    from .outline import load_outline

    outline = load_outline()
    studied = [t for t in topics if t["topic"] in acc_by_topic][:12]
    events = []
    for t in studied:
        tag = t["topic"]
        acc = acc_by_topic[tag]
        obj = outline.topic(tag)
        correct = rng.random() < acc
        events.append(
            {
                "card_id": abs(hash(tag)) % 100000,
                "topic": tag,
                "front": f"Recall a key fact about {obj.name if obj else tag}.",
                "back": (obj.name if obj else tag) + " — the core mechanism.",
                "correct": correct,
                "elapsed_ms": rng.randint(2000, 12000),
            }
        )
    return events


def _demo_palace(acc_by_topic: dict[str, float], day: int) -> list[dict]:
    """A few commissioned mnemonic scenes (offline plates rendered in-page).

    Grows with the plan; picks the weakest studied topics (the leeches). No
    files — the UI draws an engraved caption plate when ``still`` is None."""
    import time as _time

    from .palace import Leech, offline_scene_spec

    weakest = sorted(acc_by_topic.items(), key=lambda kv: kv[1])[: min(8, 2 + day // 12)]
    outline = load_outline()
    out: list[dict] = []
    for i, (tag, _acc) in enumerate(weakest):
        t = outline.topic(tag)
        name = t.name if t else tag.rsplit("::", 1)[-1]
        leech = Leech(
            card_id=90000 + i,
            front=f"What is the central idea of {name}?",
            back=f"{name} governs the mechanism the MCAT keeps testing.",
            topic=tag,
            lapses=4 + i,
            retrievability=0.2,
        )
        spec = offline_scene_spec(leech)
        out.append(
            {
                "card_id": leech.card_id,
                "topic": tag,
                "title": spec["title"],
                "scene": spec["scene"],
                "caption": spec["caption"],
                "anchors": spec["anchors"],
                "still": None,  # in-page engraved plate
                "motion": None,
                "provider": "offline-engraver",
                "created_at": _time.time() - i * 3600,
            }
        )
    return out


def _demo_palace_gallery(records: list[dict]) -> dict:
    from .palace import gallery_payload

    return gallery_payload(records)


def _demo_fl_results(day: int, days_remaining: int, now_ts: float) -> dict:
    """Synthesized full-length results: a test reads as taken once its plan
    day is behind you, with scores that show the climb (FL1 505 -> FL2 513)."""
    fl_plan_day = {1: RUNWAY - 32, 2: RUNWAY - 10}
    totals = {1: 505, 2: 513}
    sec_share = {"chem_phys": 0.24, "cars": 0.22, "bio_biochem": 0.28, "psych_soc": 0.26}
    out: dict[str, dict] = {}
    for n in (1, 2):
        if day <= fl_plan_day[n]:
            continue  # still ahead on the calendar
        total = totals[n]
        pool = total - 4 * 118
        scaled: dict[str, int] = {
            sid: 118 + min(14, round(pool * sec_share[sid])) for sid in SECTION_ORDER
        }
        first = SECTION_ORDER[0]
        scaled[first] += total - sum(scaled.values())
        secs = [
            {
                "id": sid,
                "name": SECTION_NAMES[sid],
                "n": 16,
                "correct": max(0, min(16, round(16 * (scaled[sid] - 118) / 14))),
                "scaled": scaled[sid],
            }
            for sid in SECTION_ORDER
        ]
        out[str(n)] = {
            "test_no": n,
            "sections": secs,
            "total": total,
            "taken_at": now_ts - (day - fl_plan_day[n]) * _SECOND_PER_DAY,
        }
    return out


def _demo_conf(correct: bool, overconfident: bool, rng: random.Random) -> float:
    if overconfident:
        # feels sure regardless — the danger zone
        return round(rng.uniform(0.8, 0.95), 2)
    # calibrated: confident when right, hesitant when wrong
    return round(
        rng.uniform(0.7, 0.95) if correct else rng.uniform(0.25, 0.55), 2
    )


def _demo_quiz_status(quiz_responses: dict, open_responses: dict) -> dict:
    import time as _time

    from .openended import open_progress
    from .performance_items import quiz_progress

    now = _time.time()
    qp = quiz_progress(quiz_responses, now=now)
    op = open_progress(open_responses, now=now)
    reassess = [
        d
        for d in (qp["next_reassess_days"], op["next_reassess_days"])
        if d is not None
    ]
    return {
        "total": qp["total"] + op["total"],
        "attempted": qp["attempted"] + op["attempted"],
        "proven": qp["proven"] + op["proven"],
        "due": qp["due"] + op["due"],
        "next_reassess_days": round(min(reassess), 1) if reassess else None,
    }


def _demo_jumps() -> list[dict]:
    """Named day targets the control bar offers (skip-to buttons)."""
    return [
        {"label": "First night", "day": 1},
        {"label": "Week 2", "day": 14},
        {"label": "The Bridge", "day": RUNWAY - 40},
        {"label": "The Sharpen", "day": RUNWAY - 12},
        {"label": "Final Table eve", "day": RUNWAY - 1},
    ]
