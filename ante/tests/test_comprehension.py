# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for calibration-adjusted comprehension + readiness (confident-but-wrong
pushes the interval DOWN)."""

from ante.comprehension import build_comprehension
from ante.config import CONFIG
from ante.coverage import compute_coverage
from ante.mastery import TopicStats, compute_mastery
from ante.metacognition import (
    calibration_report,
    overconfidence_penalty,
    self_trust,
)
from ante.outline import load_outline
from ante.performance_items import load_items
from ante.readiness import project_readiness

OUTLINE = load_outline()


def _overconfident_responses():
    # high confidence (0.9) but always wrong on every bio_biochem item -> strong
    # positive bias overall AND a bio_biochem section penalty specifically.
    items = [it for it in load_items() if it.topic.startswith("mcat::bio_biochem")]
    return {
        it.id: [[(it.correct_index + 1) % len(it.choices), 1.0, 0.9]] for it in items
    }


def test_overconfidence_penalty_positive_only_when_overconfident():
    cal_over = calibration_report(_overconfident_responses())
    assert cal_over["available"] and cal_over["bias"] > 0.1
    pen = overconfidence_penalty(cal_over)
    assert 0 < pen <= CONFIG.calibration_penalty_max

    # underconfident (low conf, all right) -> no downward penalty
    items = load_items()[:16]
    under = {it.id: [[it.correct_index, 1.0, 0.2]] for it in items}
    assert overconfidence_penalty(calibration_report(under)) == 0.0


def test_confident_but_wrong_lowers_readiness_and_drops_floor():
    coverage = compute_coverage({t: 10 for t in OUTLINE.all_topics()}, OUTLINE)
    sec = {
        "bio_biochem": (0.75, 0.70, 0.80),
        "chem_phys": (0.72, 0.67, 0.77),
        "psych_soc": (0.80, 0.75, 0.85),
        "cars": (0.66, 0.61, 0.71),
    }
    base = project_readiness(sec, n_reviews=1200, coverage=coverage)
    penalized = project_readiness(
        sec, n_reviews=1200, coverage=coverage, overconfidence=0.12
    )
    assert penalized.projected_total < base.projected_total
    assert penalized.total_low < base.total_low  # the floor drops (range widens down)
    assert penalized.overconfidence_applied == 0.12


def test_comprehension_band_widens_down_under_overconfidence():
    aa = "mcat::bio_biochem::amino_acids"
    stats = {aa: TopicStats(aa, 10, 8, 0.9, perf_accuracy=0.9)}
    mastery = compute_mastery(stats, OUTLINE, CONFIG)
    topic_perf = {aa: (0.9, 0.8, 0.97)}

    calm = build_comprehension(mastery, topic_perf, {"available": False}, OUTLINE)
    over = build_comprehension(
        mastery, topic_perf, calibration_report(_overconfident_responses()), OUTLINE
    )
    calm_cell = next(t for t in calm["topics"] if t["tag"] == aa)
    over_cell = next(t for t in over["topics"] if t["tag"] == aa)
    # the bio_biochem section is overconfident, so this topic's floor drops
    assert over_cell["band"][0] < calm_cell["band"][0]
    assert over_cell["comprehension"] <= calm_cell["comprehension"]


def test_comprehension_overall_reports_evidence_fraction():
    aa = "mcat::bio_biochem::amino_acids"
    stats = {aa: TopicStats(aa, 10, 8, 0.9, perf_accuracy=0.85)}
    mastery = compute_mastery(stats, OUTLINE, CONFIG)
    comp = build_comprehension(mastery, {aa: (0.85, 0.7, 0.95)}, {}, OUTLINE)
    assert comp["overall"]["available"]
    assert 0 < comp["overall"]["evidenced_weight_fraction"] < 1
    assert comp["overall"]["n_evidence_topics"] == 1


def test_comprehension_abstains_without_evidence():
    comp = build_comprehension(compute_mastery({}, OUTLINE), {}, {}, OUTLINE)
    assert not comp["overall"]["available"]


def test_self_trust_reads_direction():
    st = self_trust(calibration_report(_overconfident_responses()))
    assert st["available"] and st["direction"] == "overconfident"
