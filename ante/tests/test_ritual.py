# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the First Light / Last Light daily bookends + the night shift."""

from ante.ritual import FIRST_LIGHT, LAST_LIGHT, bookends, night_shift


def test_fresh_morning_points_at_first_light():
    st = bookends({}, now_hour=7)
    assert not st["morning"]["done"] and not st["night"]["done"]
    assert st["next"] == FIRST_LIGHT
    assert "coffee" in st["headline"]


def test_morning_reviews_complete_first_light():
    st = bookends({8: 12}, now_hour=9)
    assert st["morning"]["done"]
    assert st["morning"]["reviews"] == 12
    assert st["next"] == LAST_LIGHT
    assert not st["complete"]


def test_both_bookends_bank_the_day():
    st = bookends({7: 10, 21: 8}, now_hour=22)
    assert st["complete"]
    assert st["next"] is None
    assert "banked" in st["headline"]


def test_missed_morning_is_no_shame():
    st = bookends({}, now_hour=15)
    assert st["next"] == LAST_LIGHT
    assert "no shame" in st["headline"].lower()


def test_small_hours_reviews_earn_no_ritual_credit():
    # a 2am session is not a bookend — protecting sleep is part of the design
    st = bookends({2: 30}, now_hour=3)
    assert not st["morning"]["done"] and not st["night"]["done"]


def test_scheduled_times_come_from_the_reminder_schedule():
    sched = [
        {"kind": "retrieval", "at": "07:30"},
        {"kind": "review", "at": "14:00"},
        {"kind": "encode", "at": "21:45"},
    ]
    st = bookends({}, schedule=sched, now_hour=6)
    assert st["morning"]["at"] == "07:30"
    assert st["night"]["at"] == "21:45"


def test_defaults_when_reminders_are_off():
    st = bookends({}, schedule=[], now_hour=6)
    assert st["morning"]["at"] == "08:00"
    assert st["night"]["at"] == "21:00"


# ----- the night shift (consolidation report) --------------------------------


def test_night_shift_reports_honest_overnight_counts():
    ns = night_shift(34, 5, now_hour=8)
    assert ns["available"]
    assert ns["settled"] == 34 and ns["loose"] == 5
    assert "34" in ns["headline"]
    assert "5" in ns["detail"]


def test_night_shift_hides_outside_the_morning_and_without_evidence():
    # afternoon: the report is a morning-game surface only
    assert not night_shift(30, 4, now_hour=15)["available"]
    # nothing happened overnight: nothing is claimed
    assert not night_shift(0, 0, now_hour=8)["available"]


def test_night_shift_never_invents_a_settled_count():
    ns = night_shift(0, 7, now_hour=7)
    assert ns["available"] and ns["settled"] == 0
    assert "0" not in ns["headline"]  # no fake "0 cards banked" brag
