# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the done-for-you study plan + calendar."""

from datetime import date

from ante.mastery import MasteryStatus, TopicMastery
from ante.studyplan import build_study_plan


def _m(tag, section, weight, weakness, status=MasteryStatus.ACTIVE, cards=10):
    return TopicMastery(
        tag=tag,
        name=tag.split("::")[-1].replace("_", " ").title(),
        section_id=section,
        status=status,
        exam_weight=weight,
        cards_total=cards,
        cards_at_strength=0,
        strength_fraction=0.0,
        average_recall=0.5,
        perf_accuracy=None,
        normalized_mastery=1.0 - weakness,
        weakness=weakness,
    )


def _mastery():
    return {
        "mcat::bio_biochem::enzymes": _m(
            "mcat::bio_biochem::enzymes", "bio_biochem", 0.9, 0.8
        ),
        "mcat::bio_biochem::metabolism": _m(
            "mcat::bio_biochem::metabolism", "bio_biochem", 0.7, 0.5
        ),
        "mcat::chem_phys::thermo": _m("mcat::chem_phys::thermo", "chem_phys", 0.6, 0.6),
        "mcat::psych_soc::learning": _m(
            "mcat::psych_soc::learning", "psych_soc", 0.5, 0.4
        ),
        "mcat::cars": _m("mcat::cars", "cars", 0.8, 0.7),
        "mcat::bio_biochem::done": _m(
            "mcat::bio_biochem::done",
            "bio_biochem",
            0.9,
            0.0,
            status=MasteryStatus.MASTERED,
        ),
    }


def test_no_exam_date_is_unavailable_but_safe():
    plan = build_study_plan(_mastery(), days_remaining=None, daily_minutes=75)
    assert not plan["available"]
    assert "exam date" in plan["message"]


def test_today_prescription_is_concrete_and_decision_free():
    plan = build_study_plan(
        _mastery(), days_remaining=90, daily_minutes=75, now=date(2026, 7, 2)
    )
    assert plan["available"]
    t = plan["today"]
    # focus is a real unmastered topic (the rotation cycles weak spots)
    assert t["focus_tag"] in {
        "mcat::bio_biochem::enzymes",
        "mcat::psych_soc::learning",
        "mcat::cars",
    }
    assert t["flashcards"] > 0 and t["quiz"] > 0
    assert "flashcards" in t["prescription"]
    # mastered topics are never the focus
    assert t["focus_tag"] != "mcat::bio_biochem::done"


def test_focus_rotates_across_days():
    # the daily focus interleaves weak spots instead of pinning one topic
    focuses = {
        build_study_plan(
            _mastery(), days_remaining=90 - k, daily_minutes=75, now=date(2026, 7, 2)
        )["today"]["focus_tag"]
        for k in range(3)
    }
    assert len(focuses) >= 2


def test_calendar_has_rest_days_exam_and_today():
    plan = build_study_plan(
        _mastery(),
        days_remaining=30,
        daily_minutes=60,
        exam_date="2026-08-01",
        now=date(2026, 7, 2),
        calendar_days=21,
    )
    cal = plan["calendar"]
    assert cal[0]["is_today"] is True
    assert any(d["is_rest"] for d in cal)  # at least one rest day in 3 weeks
    # rest days carry no load
    for d in cal:
        if d["is_rest"] or d["is_exam"]:
            assert d["flashcards"] == 0 and d["quiz"] == 0
        else:
            assert d["minutes"] > 0
    # study days name a focus topic + section
    study = [d for d in cal if not d["is_rest"] and not d["is_exam"]]
    assert all(d["focus_tag"] and d["section_abbr"] for d in study)


def test_phases_span_the_runway_in_order():
    plan = build_study_plan(_mastery(), days_remaining=100, daily_minutes=75)
    tl = plan["timeline"]
    assert [p["id"] for p in tl] == ["build", "bridge", "sharpen"]
    assert tl[0]["start_offset"] == 0
    assert tl[-1]["end_offset"] == 100
    # phases are contiguous and increasing
    for a, b in zip(tl, tl[1:]):
        assert a["end_offset"] == b["start_offset"]


def test_mix_shifts_toward_application_in_later_phases():
    early = build_study_plan(_mastery(), days_remaining=100, daily_minutes=120)
    # a build-phase day (offset 0) vs a sharpen-phase day (near the end)
    cal = build_study_plan(
        _mastery(), days_remaining=100, daily_minutes=120, calendar_days=100
    )["calendar"]
    build_day = next(d for d in cal if d["phase"] == "build" and not d["is_rest"])
    sharpen_day = next(
        (d for d in cal if d["phase"] == "sharpen" and not d["is_rest"]), None
    )
    # flashcards (8s) always outnumber quizzes (50s) by raw count; what shifts
    # is the *share* of effort — the quiz:flashcard ratio rises later
    build_ratio = build_day["quiz"] / max(1, build_day["flashcards"])
    assert build_day["flashcards"] > build_day["quiz"]
    if sharpen_day:
        sharpen_ratio = sharpen_day["quiz"] / max(1, sharpen_day["flashcards"])
        assert sharpen_ratio > build_ratio
    assert early["today"]["phase"] == "build"


def test_milestones_include_sections_and_exam():
    plan = build_study_plan(
        _mastery(),
        days_remaining=60,
        daily_minutes=75,
        exam_date="2026-09-01",
        now=date(2026, 7, 2),
    )
    kinds = {m["kind"] for m in plan["milestones"]}
    assert "exam" in kinds
    assert "section_mastery" in kinds
    # the exam is the last milestone, at the end of the runway
    last = plan["milestones"][-1]
    assert last["kind"] == "exam" and last["offset"] == 60
    assert last["date"] == "2026-09-01"


def test_day_is_split_into_paced_windows():
    slot_plan = [
        {
            "window": "morning",
            "minutes": 60,
            "role": "new",
            "role_detail": "new + hardest",
        },
        {
            "window": "during the day",
            "minutes": 40,
            "role": "review",
            "role_detail": "spaced review",
        },
        {
            "window": "night",
            "minutes": 20,
            "role": "encode",
            "role_detail": "light pre-sleep",
        },
    ]
    plan = build_study_plan(
        _mastery(), days_remaining=90, daily_minutes=120, slot_plan=slot_plan
    )
    slots = plan["today"]["slots"]
    assert [s["label"] for s in slots] == ["Morning Game", "Midday Hold", "Midnight Game"]
    # each window carries its own bounded dose, and the morning (more minutes)
    # has a bigger dose than the night
    assert slots[0]["flashcards"] > slots[2]["flashcards"]
    assert all(s["flashcards"] > 0 and s["at"] for s in slots)
    # the windows sum to (roughly) the day — they're slices, not the whole thing
    assert sum(s["minutes"] for s in slots) == 120


def test_no_slot_plan_leaves_slots_empty():
    plan = build_study_plan(_mastery(), days_remaining=90, daily_minutes=120)
    assert plan["today"]["slots"] == []


def test_cleared_board_is_graceful():
    m = {
        "mcat::bio_biochem::done": _m(
            "mcat::bio_biochem::done",
            "bio_biochem",
            0.9,
            0.0,
            status=MasteryStatus.MASTERED,
        )
    }
    plan = build_study_plan(m, days_remaining=40, daily_minutes=75)
    assert plan["available"]
    assert plan["today"]["focus_tag"] is None
    assert "cleared" in plan["today"]["headline"].lower()
