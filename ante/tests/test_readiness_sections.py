# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for section-level readiness built from per-topic performance (PRD 7.3)."""

from ante.coverage import compute_coverage
from ante.outline import load_outline
from ante.readiness import (
    readiness_from_topics,
    section_accuracy_from_topics,
)

OUTLINE = load_outline()


def _full_perf(point=0.75, lo=0.70, hi=0.80):
    return {t.tag: (point, lo, hi) for t in OUTLINE.all_topic_objs()}


def test_uncovered_topics_drag_section_accuracy_down():
    # cover only one bio topic; the rest of bio is uncovered -> low section acc
    perf = {"mcat::bio_biochem::amino_acids": (0.95, 0.9, 1.0)}
    sec = section_accuracy_from_topics(perf, OUTLINE)
    bio_point, _, _ = sec["bio_biochem"]
    # one strong topic cannot lift the whole section because the rest use the
    # uncovered prior (~0.20)
    assert bio_point < 0.5


def test_full_coverage_tracks_topic_performance():
    sec = section_accuracy_from_topics(_full_perf(0.80, 0.75, 0.85), OUTLINE)
    for _sid, (p, lo, hi) in sec.items():
        assert abs(p - 0.80) < 1e-6
        assert lo <= p <= hi


def test_readiness_from_topics_projects_when_ready():
    counts = {t: 10 for t in OUTLINE.all_topics()}
    coverage = compute_coverage(counts, OUTLINE)
    report = readiness_from_topics(
        topic_perf=_full_perf(0.78, 0.73, 0.83),
        n_reviews=1500,
        coverage=coverage,
        best_next_topic="mcat::chem_phys::thermodynamics",
        outline=OUTLINE,
    )
    assert not report.abstained
    assert 472 <= report.projected_total <= 528
    assert report.total_low <= report.projected_total <= report.total_high
    assert len(report.sections) == 4
    # each section score within the MCAT section band
    for s in report.sections:
        assert 118 <= s.score <= 132


def test_readiness_from_topics_abstains_when_thin():
    # almost nothing covered -> coverage abstention fires
    perf = {"mcat::bio_biochem::amino_acids": (0.9, 0.85, 0.95)}
    coverage = compute_coverage({"mcat::bio_biochem::amino_acids": 5}, OUTLINE)
    report = readiness_from_topics(
        topic_perf=perf, n_reviews=10, coverage=coverage, outline=OUTLINE
    )
    assert report.abstained
    assert report.projected_total is None
    assert report.reasons
