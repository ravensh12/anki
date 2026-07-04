# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Pure-logic tests for the memory, performance, readiness and leakage models."""

import random

from ante.coverage import compute_coverage
from ante.leakage import find_leaks
from ante.memory import brier_score, calibrate, render_reliability_svg
from ante.outline import load_outline
from ante.performance import (
    FEATURES,
    LogisticRegression,
    evaluate,
    memory_baseline_probs,
    paraphrase_gap,
    section_accuracy_estimates,
)
from ante.readiness import project_readiness

# ----- memory calibration ---------------------------------------------------


def test_perfectly_calibrated_predictions_have_low_error():
    rng = random.Random(0)
    probs = []
    outcomes = []
    for _ in range(4000):
        p = rng.random()
        probs.append(p)
        outcomes.append(1 if rng.random() < p else 0)
    report = calibrate(probs, outcomes)
    # a well-calibrated source should have small ECE
    assert report.ece < 0.05
    assert 0.0 <= report.brier <= 0.25
    # CI brackets the observed recall
    lo, hi = report.recall_ci
    assert lo <= report.observed_recall <= hi
    # chart renders
    assert render_reliability_svg(report).startswith("<svg")


def test_overconfident_predictions_are_penalized():
    # always predicts 0.99 but only right half the time
    probs = [0.99] * 1000
    outcomes = [i % 2 for i in range(1000)]
    assert brier_score(probs, outcomes) > 0.4


# ----- performance bridge ---------------------------------------------------


def _make_perf_data(n, rng):
    """Truth depends on memory AND difficulty, so a memory-only baseline must
    underperform a model that also sees difficulty."""
    X, y = [], []
    for _ in range(n):
        mastery = rng.random()
        difficulty = rng.random()
        rt_z = rng.gauss(0, 1)
        coverage = rng.random()
        # application probability: high mastery helps, high difficulty hurts
        logit = 3.0 * (mastery - 0.5) - 3.0 * (difficulty - 0.5)
        p = 1.0 / (1.0 + pow(2.718281828, -logit))
        X.append([mastery, difficulty, rt_z, coverage])
        y.append(1 if rng.random() < p else 0)
    return X, y


def test_performance_model_beats_memory_baseline():
    rng = random.Random(1)
    assert FEATURES[0] == "topic_mastery"
    Xtr, ytr = _make_perf_data(1500, rng)
    Xte, yte = _make_perf_data(600, rng)

    model = LogisticRegression().fit(Xtr, ytr)
    model_probs = model.predict_proba(Xte)
    base_probs = memory_baseline_probs(Xte)

    model_eval = evaluate(model_probs, yte)
    base_eval = evaluate(base_probs, yte)
    # the bridge must beat copying memory on held-out log loss
    assert model_eval.log_loss < base_eval.log_loss


def test_paraphrase_gap_detects_memorization():
    # strong recall, weak application -> a real, meaningful gap
    card_recall = [0.95] * 30
    reworded = [0.6] * 30
    gap = paraphrase_gap(card_recall, reworded)
    assert abs(gap.gap - 0.35) < 1e-9
    assert gap.meaningful
    # when they track each other, no meaningful gap
    flat = paraphrase_gap([0.8] * 10, [0.79] * 10)
    assert not flat.meaningful


# ----- readiness ------------------------------------------------------------


def test_readiness_abstains_without_enough_data():
    outline = load_outline()
    coverage = compute_coverage({}, outline)  # nothing covered
    report = project_readiness(
        section_accuracy={"bio_biochem": (0.7, 0.6, 0.8)},
        n_reviews=10,
        coverage=coverage,
    )
    assert report.abstained
    assert report.projected_total is None
    assert report.reasons


def test_readiness_projects_score_with_range_when_ready():
    outline = load_outline()
    counts = {t: 10 for t in outline.all_topics()}
    coverage = compute_coverage(counts, outline)
    section_acc = {
        "bio_biochem": (0.75, 0.70, 0.80),
        "chem_phys": (0.70, 0.65, 0.75),
        "psych_soc": (0.80, 0.75, 0.85),
        "cars": (0.65, 0.60, 0.70),
    }
    report = project_readiness(
        section_accuracy=section_acc,
        n_reviews=1200,
        coverage=coverage,
        best_next_topic="mcat::chem_phys::thermodynamics",
    )
    assert not report.abstained
    assert 472 <= report.projected_total <= 528
    assert report.total_low <= report.projected_total <= report.total_high
    assert report.confidence in {"low", "medium", "high"}
    assert len(report.sections) == 4
    assert report.best_next_topic == "mcat::chem_phys::thermodynamics"


def test_section_accuracy_estimates_have_ci():
    est = section_accuracy_estimates({"bio_biochem": [0.7, 0.8, 0.6, 0.9, 0.75]})
    point, lo, hi = est["bio_biochem"]
    assert lo <= point <= hi


# ----- leakage --------------------------------------------------------------


def test_leakage_detects_exact_and_near_duplicates():
    train = [
        "The mitochondria is the powerhouse of the cell",
        "Glycolysis nets two ATP per glucose molecule",
        "Water boils at one hundred degrees celsius",
    ]
    test = [
        "The mitochondria is the powerhouse of the cell",  # exact
        "Glycolysis nets two ATP molecules per glucose",  # near
        "Photosynthesis occurs in the chloroplast",  # clean
    ]
    leaks = find_leaks(train, test, threshold=0.5)
    leaked_test_indices = {leak.test_index for leak in leaks}
    assert 0 in leaked_test_indices  # exact
    assert 2 not in leaked_test_indices  # clean stays clean


def test_clean_sets_have_no_leaks():
    train = ["alpha beta gamma", "delta epsilon zeta"]
    test = ["completely different content here", "nothing overlapping at all friend"]
    assert find_leaks(train, test) == []
