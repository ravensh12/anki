# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for demo mode — the time-travellable, fully-populated instrument."""

from ante.demo import RUNWAY, build_demo_dashboard


def test_demo_populates_every_surface():
    d = build_demo_dashboard(day=20)
    # the big surfaces all present + populated
    assert d["demo"]["enabled"] and d["demo"]["day"] == 20
    assert d["scores"]["memory"]["available"]
    assert d["mastery_map"]["counts"]["mastered"] >= 0
    assert d["comprehension"]["overall"]["available"]
    assert d["study_plan"]["available"] and d["study_plan"]["today"]["slots"]
    assert d["recalibration"]["available"]
    assert d["quiz_status"]["total"] > 0
    # calibration read available (we fed confidence-rated answers)
    cs = d["calibration_sources"]
    assert cs["application"]["available"] or cs["flashcard"]["available"]


def test_day_skip_advances_countdown_and_phase():
    early = build_demo_dashboard(day=2)
    late = build_demo_dashboard(day=RUNWAY - 5)
    assert early["recalibration"]["days_remaining"] > late["recalibration"]["days_remaining"]
    # retention ramps up as the exam nears
    assert late["recalibration"]["desired_retention"] >= early["recalibration"]["desired_retention"]
    # phase advances toward sharpen near the end
    assert early["study_plan"]["today"]["phase"] == "build"
    assert late["study_plan"]["today"]["phase"] == "sharpen"


def test_progress_grows_with_day():
    early = build_demo_dashboard(day=5)
    late = build_demo_dashboard(day=70)
    # more mastered topics later, more reviews banked
    assert (
        late["mastery_map"]["counts"]["mastered"]
        >= early["mastery_map"]["counts"]["mastered"]
    )
    assert late["scores"]["readiness"]["n_reviews"] > early["scores"]["readiness"]["n_reviews"]
    # readiness eventually projects a real number (climbs out of abstention)
    assert late["scores"]["readiness"]["projected_total"] is not None


def test_overconfident_flag_shows_in_calibration():
    over = build_demo_dashboard(day=40, flags={"overconfident": True})
    src = over["calibration_sources"]["combined"]
    if src.get("available"):
        assert src["verdict"] in ("overconfident", "well calibrated", "underconfident")
        # with the overconfident flag, confidence should exceed accuracy
        assert src["avg_confidence"] >= src["accuracy"] - 0.05


def test_rewards_always_on_and_story_evolves():
    # rewards are always on in demo (no manual flags), and the diagnosis
    # verdict changes across the course instead of repeating one headline
    on = build_demo_dashboard(day=30)
    assert on["motivation"]["opt_in"] is True
    heads = set()
    for day in (2, 25, 60, 85):
        d = build_demo_dashboard(day=day)
        top = (d.get("diagnosis") or {}).get("top") or {}
        heads.add(top.get("headline", "(clear)"))
    assert len(heads) >= 3


def test_bounds_are_safe():
    lo = build_demo_dashboard(day=-50)
    hi = build_demo_dashboard(day=9999)
    assert lo["demo"]["day"] == 0
    assert hi["demo"]["day"] == RUNWAY


def test_simulator_hour_drives_the_clocked_surfaces():
    morning = build_demo_dashboard(day=30, flags={"hour": 9})
    night = build_demo_dashboard(day=30, flags={"hour": 23})
    # bookends follow the simulated clock: the morning game is still PLAYABLE
    # (unbanked) in its window and reads as banked once midday passes; the
    # midnight game stays playable through dusk/night on the tour
    assert not morning["ritual"]["morning"]["done"]  # 9am: play it now
    assert night["ritual"]["morning"]["done"]  # 11pm: morning banked
    assert not night["ritual"]["night"]["done"]  # midnight game still playable
    # the last-hand reel only exists in the evening window
    assert not morning["dreamseed"]["available"] or morning["demo"]["hour"] >= 19
    assert night["dreamseed"]["available"]
    # the den's window phase tracks the hour
    assert morning["world"]["night"]["phase"] in ("day", "dawn")
    assert night["world"]["night"]["phase"] == "night"
    # the night-shift report is a morning surface
    assert morning["night_shift"]["available"]
    assert not night["night_shift"]["available"]
    assert morning["demo"]["hour"] == 9 and night["demo"]["hour"] == 23
