# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the exam-date recalibration engine + profile + reminders."""

from datetime import date, timedelta

from ante.config import CONFIG
from ante.profile import StudyProfile
from ante.recalibrate import desired_retention_for, recalibrate
from ante.reminders import build_schedule, next_reminder, what_to_do_now


def _exam_in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_profile_from_dict_clamps_and_defaults():
    p = StudyProfile.from_dict({"daily_minutes": 9999, "chronotype": "bogus"})
    assert p.daily_minutes == CONFIG.max_daily_minutes
    assert p.chronotype == "neutral"
    assert p.study_windows  # non-empty default


def test_quiet_hours_wrap_midnight():
    p = StudyProfile(quiet_start_hour=22, quiet_end_hour=7)
    assert p.in_quiet_hours(23) and p.in_quiet_hours(3)
    assert not p.in_quiet_hours(9)


def test_desired_retention_ramps_up_as_exam_nears():
    far = desired_retention_for(120, CONFIG)
    near = desired_retention_for(5, CONFIG)
    assert far == round(CONFIG.retention_floor, 2)
    assert near > far
    assert near <= round(CONFIG.retention_ceiling, 2)
    assert desired_retention_for(None, CONFIG) == round(CONFIG.retention_floor, 2)


def test_recalibrate_projects_full_plan():
    prof = StudyProfile(exam_date=_exam_in(90), target_score=512, daily_minutes=60)
    plan = recalibrate(prof, due_count=40, topics_remaining=20, remaining_minutes=1800)
    assert plan.available
    assert abs(plan.days_remaining - 90) <= 1
    assert (
        CONFIG.min_daily_minutes
        <= plan.recommended_daily_minutes
        <= CONFIG.max_daily_minutes
    )
    assert plan.desired_retention == round(CONFIG.retention_floor, 2)  # far out
    assert plan.max_interval_days == plan.days_remaining
    # the day is split across the chosen windows and sums to the budget
    assert sum(s["minutes"] for s in plan.slot_plan) == plan.recommended_daily_minutes
    assert plan.pacing["available"]


def test_recalibrate_without_date_is_unscheduled_but_safe():
    plan = recalibrate(StudyProfile(daily_minutes=45), due_count=10)
    assert not plan.available
    assert plan.days_remaining is None
    assert plan.max_interval_days is None
    assert plan.recommended_daily_minutes == 45


def test_crunch_intensity_when_close():
    prof = StudyProfile(exam_date=_exam_in(5), daily_minutes=60)
    plan = recalibrate(prof, due_count=100, topics_remaining=10, remaining_minutes=1200)
    assert plan.intensity == "crunch"
    assert plan.desired_retention > CONFIG.retention_floor


def test_diagnostic_baseline_prices_the_climb_into_the_budget():
    prof = StudyProfile(exam_date=_exam_in(60), target_score=515, daily_minutes=60)
    without = recalibrate(
        prof, due_count=20, topics_remaining=15, remaining_minutes=2400
    )
    with_gap = recalibrate(
        prof,
        due_count=20,
        topics_remaining=15,
        remaining_minutes=2400,
        baseline_total=498,  # a 17-point climb
    )
    assert with_gap.target_gap == 17
    assert with_gap.baseline_total == 498
    assert with_gap.recommended_daily_minutes > without.recommended_daily_minutes
    assert "climb" in with_gap.pacing["message"]
    d = with_gap.as_dict()
    assert d["target_gap"] == 17 and d["baseline_total"] == 498


def test_baseline_at_or_above_target_never_manufactures_urgency():
    prof = StudyProfile(exam_date=_exam_in(60), target_score=510, daily_minutes=60)
    plain = recalibrate(prof, due_count=20, topics_remaining=15, remaining_minutes=2400)
    ahead = recalibrate(
        prof,
        due_count=20,
        topics_remaining=15,
        remaining_minutes=2400,
        baseline_total=514,
    )
    assert ahead.target_gap == -4
    assert ahead.recommended_daily_minutes == plain.recommended_daily_minutes
    assert "holds the line" in ahead.pacing["message"]


def test_chronotype_places_new_material_in_peak_window():
    lark = recalibrate(
        StudyProfile(exam_date=_exam_in(30), chronotype="lark", daily_minutes=90),
        remaining_minutes=600,
        topics_remaining=10,
    )
    roles = {s["window"]: s["role"] for s in lark.slot_plan}
    assert roles.get("morning") == "new"  # larks get new material in the morning
    assert roles.get("night") == "encode"  # night is always pre-sleep review


def test_reminders_are_cue_anchored_and_respect_quiet_hours():
    prof = StudyProfile(
        exam_date=_exam_in(30), daily_minutes=75, reminders_enabled=True
    )
    plan = recalibrate(prof, due_count=40, remaining_minutes=600, topics_remaining=8)
    sched = build_schedule(
        prof, plan.slot_plan, due_count=40, days_remaining=plan.days_remaining
    )
    assert sched
    kinds = {r.kind for r in sched}
    assert "encode" in kinds  # the pre-sleep "last hand" nudge
    night = next(r for r in sched if r.kind == "encode")
    assert "lights out" in night.title.lower() or "overnight" in night.body.lower()
    # sorted by time
    mins = [r.minutes_of_day for r in sched]
    assert mins == sorted(mins)


def test_reminders_off_returns_empty():
    prof = StudyProfile(exam_date=_exam_in(30), reminders_enabled=False)
    plan = recalibrate(prof, due_count=40)
    assert build_schedule(prof, plan.slot_plan, due_count=40) == []


def test_what_to_do_now_gives_a_right_sized_bite():
    now = what_to_do_now(
        due_count=200,
        best_next_topic="mcat::bio_biochem::enzymes",
        recommended_daily_minutes=75,
        now_hour=14,
    )
    assert now["cards"] > 0 and now["minutes"] > 0
    clear = what_to_do_now(
        due_count=0, best_next_topic=None, recommended_daily_minutes=75, now_hour=14
    )
    assert clear["cards"] == 0


def test_next_reminder_wraps_to_tomorrow():
    prof = StudyProfile(exam_date=_exam_in(30), daily_minutes=75)
    plan = recalibrate(prof, due_count=40)
    sched = build_schedule(prof, plan.slot_plan, due_count=40)
    # after the last reminder, the next one wraps to the first
    assert next_reminder(sched, 23) is sched[0]


def test_marked_night_becomes_a_dated_early_evening_reminder():
    prof = StudyProfile(
        exam_date=_exam_in(30), daily_minutes=75, reminders_enabled=True
    )
    plan = recalibrate(prof, due_count=40, remaining_minutes=600, topics_remaining=8)
    night = {"kind": "practice_test", "offset": 4, "date": "2026-08-01"}
    sched = build_schedule(prof, plan.slot_plan, due_count=40, marked_night=night)
    marked = [r for r in sched if r.kind == "checkpoint"]
    assert len(marked) == 1
    r = marked[0]
    assert (r.hour, r.minute) == (17, 0)
    assert r.date == "2026-08-01"
    assert "checkpoint" in r.title.lower()
    assert r.as_dict()["date"] == "2026-08-01"
    # daily reminders carry no date
    assert all(x.date is None for x in sched if x.kind != "checkpoint")

    # next_reminder never offers a marked night that isn't tonight...
    nxt = next_reminder(sched, 16, 0, today="2026-07-28")
    assert nxt is not None and nxt.kind != "checkpoint"
    # ...but on its night it is the 17:00 cue
    tonight = next_reminder(sched, 16, 0, today="2026-08-01")
    assert tonight is not None and tonight.kind == "checkpoint"


def test_full_length_marked_night_gets_its_own_copy():
    prof = StudyProfile(exam_date=_exam_in(30), reminders_enabled=True)
    plan = recalibrate(prof, due_count=0)
    sched = build_schedule(
        prof,
        plan.slot_plan,
        marked_night={"kind": "full_length", "test_no": 2, "date": "2026-08-04"},
    )
    r = next(x for x in sched if x.kind == "checkpoint")
    assert "full-length 2" in r.title.lower()
    assert "dress rehearsal" in r.body.lower()
