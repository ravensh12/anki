# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the dashboard assembly and the 'time back' planner."""

from ante.app import best_next_topic, build_dashboard, time_back_plan
from ante.outline import load_outline


def _topics(**overrides):
    outline = load_outline()
    topics = []
    for t in outline.all_topics():
        topics.append(
            {
                "topic": t,
                "weight": outline.topic_weight(t),
                "total_cards": 5,
                "studied_cards": 5,
                "mastered_cards": 3,
                "average_recall": 0.8,
                "coverage": 1.0,
            }
        )
    for t in topics:
        if t["topic"] in overrides:
            t.update(overrides[t["topic"]])
    return topics


def test_time_back_plan_covers_light_load():
    plan = time_back_plan(due_count=200, new_count=0, budget_minutes=75)
    # 75 min @ 8s/card = ~562 cards capacity, easily covers 200
    assert plan["covers_due_load"]
    assert plan["daily_capacity_cards"] > 200
    assert sum(s["cards"] for s in plan["slots"]) == 200
    assert "no marathon" in plan["message"]


def test_time_back_plan_flags_overflow():
    plan = time_back_plan(due_count=2000, new_count=0, budget_minutes=30)
    assert not plan["covers_due_load"]
    assert plan["due_minutes_needed"] > 30
    # slots only schedule up to capacity
    assert sum(s["cards"] for s in plan["slots"]) == plan["daily_capacity_cards"]


def test_best_next_topic_prefers_high_weight_weakness():
    topics = _topics(
        **{
            "mcat::bio_biochem::enzymes": {"average_recall": 0.2},  # weak + high weight
            "mcat::cars": {"average_recall": 0.1},  # weak but low weight
        }
    )
    assert best_next_topic(topics) == "mcat::bio_biochem::enzymes"


def test_dashboard_shows_memory_but_abstains_on_performance_and_readiness():
    topics = _topics()
    dash = build_dashboard(
        topics, due_count=120, new_count=20, n_reviews=50, budget_minutes=75
    )
    # memory is computed and ranged
    assert dash["scores"]["memory"]["available"]
    assert dash["scores"]["memory"]["range"][0] <= dash["scores"]["memory"]["recall"]
    # performance abstains without exam-style eval data
    assert not dash["scores"]["performance"]["available"]
    # readiness abstains (no performance + too few reviews)
    assert dash["scores"]["readiness"]["abstained"]
    assert dash["scores"]["readiness"]["projected_total"] is None
    # time-back present and best-next-topic set
    assert dash["time_back"]["budget_minutes"] == 75
    assert dash["best_next_topic"] is not None


def test_dashboard_projects_readiness_with_performance_data():
    topics = _topics()
    section_perf = {
        "bio_biochem": (0.78, 0.73, 0.83),
        "chem_phys": (0.72, 0.67, 0.77),
        "psych_soc": (0.80, 0.75, 0.85),
        "cars": (0.66, 0.61, 0.71),
    }
    dash = build_dashboard(
        topics,
        due_count=120,
        new_count=0,
        n_reviews=1500,
        budget_minutes=75,
        section_performance=section_perf,
    )
    assert dash["scores"]["performance"]["available"]
    r = dash["scores"]["readiness"]
    assert not r["abstained"]
    assert 472 <= r["projected_total"] <= 528
    assert r["total_range"][0] <= r["projected_total"] <= r["total_range"][1]


def test_dashboard_carries_ritual_bookends():
    dash = build_dashboard(
        _topics(),
        due_count=40,
        new_count=0,
        n_reviews=50,
        hour_counts_today={8: 12},
        now_hour=10,
    )
    rit = dash["ritual"]
    assert rit["morning"]["done"] is True
    assert rit["night"]["done"] is False
    assert rit["next"] == "last_light"


def test_dashboard_announces_the_next_marked_night_as_a_dated_reminder():
    from datetime import date, timedelta

    # 42 days out: the exam-anchored 14-day cadence lands a quiz checkpoint
    # on TONIGHT, and no full-length shares the night
    today = date.today()
    dash = build_dashboard(
        _topics(),
        due_count=40,
        new_count=0,
        n_reviews=50,
        profile={"exam_date": (today + timedelta(days=42)).isoformat()},
    )
    marked = [r for r in dash["reminders"]["schedule"] if r["kind"] == "checkpoint"]
    assert len(marked) == 1
    assert marked[0]["date"] == today.isoformat()
    assert marked[0]["at"] == "17:00"
    assert "checkpoint" in marked[0]["title"].lower()
    # the plan's milestones agree: a marked night sits at offset 0 for the den
    tonight = [m for m in dash["study_plan"]["milestones"] if m["offset"] == 0]
    assert any(m["kind"] == "practice_test" for m in tonight)


def test_dashboard_folds_diagnostic_baseline_into_the_plan():
    import time

    from ante.diagnostic import build_diagnostic
    from ante.performance_items import item_by_id

    form = build_diagnostic()
    # answer the bio + chem/phys sections correctly, others wrong, to get a
    # scoreable baseline in enough sections that the climb is priced in
    now = time.time()
    mcq: dict[str, list] = {}
    opn: dict[str, list] = {}
    for sec in form.sections:
        right = sec.id in {"bio_biochem", "chem_phys", "psych_soc", "cars"}
        for it in sec.items:
            if it["type"] == "mcq":
                meta = item_by_id(it["id"])
                choice = meta.correct_index if right else (meta.correct_index + 1) % 4
                mcq[it["id"]] = [[choice, now, 0.7, 6000]]
            else:
                opn[it["id"]] = [[1.0 if right else 0.0, now, 0.7, 15000]]

    dash = build_dashboard(
        _topics(),
        due_count=40,
        new_count=0,
        n_reviews=50,
        target_score=520,
        quiz_responses=mcq,
        open_responses=opn,
        diagnostic={"taken_at": now, "item_ids": form.item_ids},
    )
    diag = dash["diagnostic"]
    assert diag["taken"] and diag["summary"]["available"]
    # baseline computed and the climb reflected on the plan
    assert dash["recalibration"]["baseline_total"] is not None
