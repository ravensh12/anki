# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the exam-date readiness forecast (The Trajectory)."""

from ante.coverage import compute_coverage
from ante.forecast import build_forecast, days_until, topic_remaining_minutes
from ante.mastery import MasteryStatus, TopicStats, compute_mastery
from ante.outline import load_outline


def _full_scenario(perf_default=0.55, mastered=()):
    """Every outline topic has cards; a few are fully mastered, rest are weak."""
    outline = load_outline()
    tags = outline.all_topics()
    counts = {t: 12 for t in tags}
    coverage = compute_coverage(counts, outline)
    stats = {}
    perf = {}
    for t in tags:
        if t in mastered:
            stats[t] = TopicStats(t, 12, 12, 0.95, perf_accuracy=0.9)
            perf[t] = (0.9, 0.85, 0.95)
        else:
            stats[t] = TopicStats(t, 12, 4, 0.6, perf_accuracy=perf_default)
            perf[t] = (perf_default, perf_default - 0.1, perf_default + 0.1)
    mastery = compute_mastery(stats, outline)
    return outline, coverage, mastery, perf, counts


def test_days_until_parses_and_counts():
    import datetime
    import time as _t

    # build "now" from a local calendar noon so the count is timezone-robust
    now = _t.mktime(datetime.datetime(2020, 1, 1, 12, 0, 0).timetuple())
    assert days_until("2020-01-11", now=now) == 10
    assert days_until(None) is None
    assert days_until("not-a-date") is None


def test_forecast_requires_exam_date():
    outline, coverage, mastery, perf, counts = _full_scenario()
    rep = build_forecast(
        mastery,
        perf,
        coverage,
        n_reviews=1500,
        remaining_work={},
        days_remaining=None,
        daily_minutes=75,
        topic_card_counts=counts,
        outline=outline,
    )
    assert rep.available is False
    assert "exam date" in (rep.reason or "").lower()


def test_forecast_projects_and_ranks_wins():
    outline, coverage, mastery, perf, counts = _full_scenario(perf_default=0.5)
    work = {t: 60.0 for t in mastery}  # 1 hour per topic
    rep = build_forecast(
        mastery,
        perf,
        coverage,
        n_reviews=1500,
        remaining_work=work,
        days_remaining=30,
        daily_minutes=75,
        topic_card_counts=counts,
        outline=outline,
    )
    assert rep.available is True
    assert rep.days_remaining == 30
    # projecting mastery forward should not lower the score vs studying nothing
    assert rep.projected_total is not None
    assert rep.current_total is not None
    assert rep.projected_total >= rep.current_total
    # biggest wins present, positive, and sorted by points desc
    assert rep.wins
    pts = [w.points for w in rep.wins]
    assert pts == sorted(pts, reverse=True)
    assert all(w.points >= 0 for w in rep.wins)
    assert any(w.points > 0 for w in rep.wins)


def test_budget_limits_masterable_topics():
    outline, coverage, mastery, perf, counts = _full_scenario()
    work = {t: 120.0 for t in mastery}  # 2 hours each
    tight = build_forecast(
        mastery,
        perf,
        coverage,
        n_reviews=1500,
        remaining_work=work,
        days_remaining=10,
        daily_minutes=20,
        topic_card_counts=counts,
        outline=outline,  # 200 min total
    )
    roomy = build_forecast(
        mastery,
        perf,
        coverage,
        n_reviews=1500,
        remaining_work=work,
        days_remaining=60,
        daily_minutes=120,
        topic_card_counts=counts,
        outline=outline,  # 7200 min total
    )
    # 200 min / 120 per topic -> at most 1 topic
    assert tight.topics_masterable <= 1
    assert roomy.topics_masterable > tight.topics_masterable
    assert tight.topics_masterable <= tight.topics_remaining


def test_target_planning_reports_pace():
    outline, coverage, mastery, perf, counts = _full_scenario(perf_default=0.45)
    work = {t: 45.0 for t in mastery}
    rep = build_forecast(
        mastery,
        perf,
        coverage,
        n_reviews=1500,
        remaining_work=work,
        days_remaining=40,
        daily_minutes=90,
        target_score=505,
        topic_card_counts=counts,
        outline=outline,
    )
    assert rep.target_score == 505
    assert rep.on_track is not None
    # if a plan exists to reach the target, a daily pace is reported
    if rep.on_track:
        assert rep.required_daily_minutes is not None
        assert rep.required_daily_minutes > 0


def test_topic_remaining_minutes_scales_with_gaps():
    outline, coverage, mastery, perf, counts = _full_scenario()
    weak = next(m for m in mastery.values() if m.status != MasteryStatus.MASTERED)
    strong_cards = topic_remaining_minutes(weak, open_items=0)
    with_items = topic_remaining_minutes(weak, open_items=4)
    assert with_items > strong_cards >= 0
