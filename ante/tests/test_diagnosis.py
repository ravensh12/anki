# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for The Diagnosis (cross-topic bottleneck autopsy)."""

from ante.diagnosis import diagnose
from ante.mastery import TopicStats, compute_mastery
from ante.outline import load_outline


def _mastery_with_nothing_mastered():
    outline = load_outline()
    stats = {
        t: TopicStats(t, 10, 2, 0.6, perf_accuracy=0.4) for t in outline.all_topics()
    }
    return compute_mastery(stats, outline)


def test_transfer_gap_insight_surfaces():
    mastery = _mastery_with_nothing_mastered()
    gaps = [
        {
            "topic": "mcat::bio_biochem::amino_acids",
            "recall": 0.95,
            "application": 0.3,
            "gap": 0.65,
        },
        {
            "topic": "mcat::bio_biochem::enzymes",
            "recall": 0.9,
            "application": 0.4,
            "gap": 0.5,
        },
    ]
    rep = diagnose(mastery, gaps)
    kinds = {i["kind"] for i in rep["insights"]}
    assert "transfer_gap" in kinds
    assert rep["available"] and rep["top"] is not None


def test_keystone_prereq_detected():
    # nothing mastered -> topics with prereqs are locked and blocked; a foundational
    # prereq (e.g. nucleic_acids) blocks several downstream topics
    mastery = _mastery_with_nothing_mastered()
    rep = diagnose(mastery, gaps=[])
    keystones = [i for i in rep["insights"] if i["kind"] == "keystone"]
    assert keystones, "expected a keystone-prereq insight when nothing is mastered"
    assert "gating" in keystones[0]["headline"]


def test_overconfidence_insight_from_calibration():
    mastery = _mastery_with_nothing_mastered()
    calib = {"available": True, "bias": 0.3, "worst_section": "psych_soc"}
    rep = diagnose(mastery, gaps=[], calibration=calib)
    over = [i for i in rep["insights"] if i["kind"] == "overconfidence"]
    assert over and "Psych/Soc" in over[0]["detail"]


def test_insights_sorted_by_severity():
    mastery = _mastery_with_nothing_mastered()
    gaps = [
        {
            "topic": "mcat::bio_biochem::amino_acids",
            "recall": 0.95,
            "application": 0.2,
            "gap": 0.75,
        },
        {
            "topic": "mcat::bio_biochem::enzymes",
            "recall": 0.9,
            "application": 0.3,
            "gap": 0.6,
        },
    ]
    rep = diagnose(mastery, gaps, calibration={"available": True, "bias": 0.3})
    sev = [i["severity"] for i in rep["insights"]]
    assert sev == sorted(sev, reverse=True)


def test_no_insights_when_clean():
    outline = load_outline()
    # everything mastered, no gaps, good calibration
    stats = {
        t: TopicStats(t, 10, 10, 0.97, perf_accuracy=0.9) for t in outline.all_topics()
    }
    mastery = compute_mastery(stats, outline)
    rep = diagnose(mastery, gaps=[], calibration={"available": True, "bias": 0.0})
    assert rep["available"] is False
    assert rep["top"] is None
