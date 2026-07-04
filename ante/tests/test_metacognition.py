# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for confidence calibration (metacognition)."""

from ante.metacognition import (
    MIN_RATED,
    calibration_comparison,
    calibration_report,
    combined_calibration,
    flashcard_calibration,
)
from ante.performance_items import load_items


def _wrong(it):
    return (it.correct_index + 1) % len(it.choices)


def test_abstains_without_enough_rated_answers():
    items = load_items()
    resp = {items[0].id: [[items[0].correct_index, 1.0, 0.9]]}
    rep = calibration_report(resp)
    assert rep["available"] is False
    assert rep["n"] == 1


def test_detects_overconfidence():
    items = load_items()[:12]
    # high confidence but wrong on every item -> strongly overconfident
    resp = {it.id: [[_wrong(it), 1.0, 0.9]] for it in items}
    rep = calibration_report(resp)
    assert rep["available"] is True
    assert rep["n"] >= MIN_RATED
    assert rep["verdict"] == "overconfident"
    assert rep["bias"] > 0.1  # said ~0.9, got ~0.0
    assert 0 <= rep["score"] <= 100
    assert rep["per_section"] and rep["bins"]


def test_detects_underconfidence():
    items = load_items()[:12]
    # low confidence but correct every time -> underconfident
    resp = {it.id: [[it.correct_index, 1.0, 0.3]] for it in items}
    rep = calibration_report(resp)
    assert rep["verdict"] == "underconfident"
    assert rep["bias"] < -0.1


def test_well_calibrated_scores_high():
    items = load_items()[:16]
    resp = {}
    # confident+correct for half, unsure+wrong for half -> confidence tracks truth
    for i, it in enumerate(items):
        if i % 2 == 0:
            resp[it.id] = [[it.correct_index, 1.0, 0.9]]
        else:
            resp[it.id] = [[_wrong(it), 1.0, 0.2]]
    rep = calibration_report(resp)
    assert rep["available"] is True
    assert abs(rep["bias"]) < 0.15
    assert rep["verdict"] == "well calibrated"
    assert rep["score"] >= 60


def test_reliability_bins_track_predicted_vs_actual():
    items = load_items()[:10]
    resp = {
        it.id: [[it.correct_index, 1.0, 0.9]] for it in items
    }  # 0.9 conf, all right
    rep = calibration_report(resp)
    top = [b for b in rep["bins"] if b["lo"] >= 0.85]
    assert top and abs(top[0]["actual"] - 1.0) < 1e-9


# --- flashcard calibration (pre-flip confidence vs recall) -----------------


def _flash(conf, correct, topic="mcat::bio_biochem::enzymes"):
    return [conf, 1 if correct else 0, 0.0, topic, 0]


def test_flashcard_calibration_detects_overconfidence():
    # "know it" (0.9) before flipping, but failed to recall every time
    log = [_flash(0.9, False) for _ in range(10)]
    rep = flashcard_calibration(log)
    assert rep["available"] and rep["source"] == "flashcard"
    assert rep["verdict"] == "overconfident"
    assert rep["bias"] > 0.1


def test_flashcard_calibration_abstains_when_sparse():
    rep = flashcard_calibration([_flash(0.9, True)])
    assert rep["available"] is False and rep["n"] == 1


def test_combined_calibration_pools_flash_and_quiz():
    items = load_items()[:6]
    quiz = {it.id: [[it.correct_index, 1.0, 0.9]] for it in items}
    flash = [_flash(0.9, False) for _ in range(6)]
    combined = combined_calibration(quiz, {}, flash)
    assert combined["available"] and combined["source"] == "combined"
    assert combined["n"] >= MIN_RATED


def test_comparison_flags_familiarity_trap():
    # overconfident on flashcards, well-calibrated on the quiz -> positive gap
    flash = flashcard_calibration([_flash(0.9, False) for _ in range(10)])
    items = load_items()[:16]
    quiz = calibration_report(
        {
            it.id: [[it.correct_index, 1.0, 0.9]]
            if i % 2 == 0
            else [[(it.correct_index + 1) % len(it.choices), 1.0, 0.2]]
            for i, it in enumerate(items)
        }
    )
    cmp = calibration_comparison(flash, quiz)
    assert cmp["available"] and cmp["gap"] > 0.08

    # abstains cleanly when a side lacks data
    assert calibration_comparison(flash, {"available": False})["available"] is False
