# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the readiness track record (spec section 1 honesty rule)."""

from ante.trackrecord import SECONDS_PER_DAY, append_line, evaluate


def _line(total, lo, hi, abstained=False):
    return {
        "abstained": abstained,
        "projected_total": None if abstained else total,
        "total_range": None if abstained else [lo, hi],
        "confidence": "low",
    }


def test_append_records_only_real_lines():
    hist = []
    hist = append_line(hist, _line(508, 503, 512), now=0.0)
    assert len(hist) == 1
    # an abstaining line is not recorded
    hist = append_line(hist, _line(0, 0, 0, abstained=True), now=10 * SECONDS_PER_DAY)
    assert len(hist) == 1
    # a line with no range is not recorded
    no_range = {"abstained": False, "projected_total": 510, "total_range": None}
    hist = append_line(hist, no_range, now=11 * SECONDS_PER_DAY)
    assert len(hist) == 1


def test_append_dedupes_within_a_day_but_grows_across_days():
    hist = []
    hist = append_line(hist, _line(500, 495, 505), now=0.0)
    # same day -> replaces
    hist = append_line(hist, _line(502, 497, 507), now=0.5 * SECONDS_PER_DAY)
    assert len(hist) == 1
    assert hist[-1]["projected_total"] == 502
    # next day -> appends
    hist = append_line(hist, _line(506, 501, 511), now=2 * SECONDS_PER_DAY)
    assert len(hist) == 2


def test_evaluate_abstains_without_completed_tests():
    hist = [{"ts": 0.0, "projected_total": 508, "low": 503, "high": 512}]
    tr = evaluate(hist, fl_results={})
    assert tr.abstained
    assert tr.hit_rate is None
    assert "Not enough" in tr.summary()


def test_evaluate_scores_line_against_next_actual():
    # posted a line at day 0; took a full-length at day 5 scoring 509 (in range)
    hist = [{"ts": 0.0, "projected_total": 508, "low": 503, "high": 512}]
    fl = {"1": {"total": 509, "taken_at": 5 * SECONDS_PER_DAY}}
    tr = evaluate(hist, fl)
    assert not tr.abstained
    assert tr.n_checks == 1
    assert tr.n_within_range == 1
    assert tr.hit_rate == 1.0
    assert tr.mean_abs_error == 1.0


def test_evaluate_flags_a_miss_and_averages_error():
    hist = [
        {"ts": 0.0, "projected_total": 520, "low": 516, "high": 524},
        {"ts": 10 * SECONDS_PER_DAY, "projected_total": 505, "low": 500, "high": 510},
    ]
    fl = {
        "1": {"total": 508, "taken_at": 3 * SECONDS_PER_DAY},  # vs 520 -> miss, err 12
        "2": {"total": 507, "taken_at": 12 * SECONDS_PER_DAY},  # vs 505 -> hit, err 2
    }
    tr = evaluate(hist, fl)
    assert tr.n_checks == 2
    assert tr.n_within_range == 1
    assert tr.mean_abs_error == 7.0  # (12 + 2) / 2


def test_evaluate_ignores_actuals_before_the_line_and_past_horizon():
    hist = [{"ts": 30 * SECONDS_PER_DAY, "projected_total": 510, "low": 505, "high": 515}]
    # an actual BEFORE the posted line must not be paired
    before = {"1": {"total": 500, "taken_at": 5 * SECONDS_PER_DAY}}
    assert evaluate(hist, before).n_checks == 0
    # an actual far beyond the horizon must not be paired
    far = {"1": {"total": 500, "taken_at": 30 * SECONDS_PER_DAY + 100 * SECONDS_PER_DAY}}
    assert evaluate(hist, far).n_checks == 0
