# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Pure-logic tests for the outline + coverage map (no Anki required)."""

from ante.coverage import (
    DEFAULT_MIN_COVERAGE,
    compute_coverage,
)
from ante.outline import load_outline


def test_outline_loads_with_expected_sections():
    outline = load_outline()
    ids = {s.id for s in outline.sections}
    assert ids == {"bio_biochem", "chem_phys", "psych_soc", "cars"}
    # CARS uses the bare bucket tag
    assert "mcat::cars" in outline.all_topics()
    # content topics use the section::category form
    assert "mcat::bio_biochem::enzymes" in outline.all_topics()
    # weights agree with the Rust section_weight table
    assert outline.topic_weight("mcat::bio_biochem::enzymes") == 1.30
    assert outline.topic_weight("mcat::cars") == 0.80


def test_empty_deck_abstains():
    report = compute_coverage({})
    assert report.covered_topics == 0
    assert report.weighted_coverage == 0.0
    assert report.abstains()
    assert report.reasons()


def test_full_coverage_does_not_abstain():
    outline = load_outline()
    counts = {t: 3 for t in outline.all_topics()}
    report = compute_coverage(counts, outline)
    assert report.overall_coverage == 1.0
    assert report.weighted_coverage == 1.0
    assert not report.abstains()
    assert report.reasons() == []


def test_high_weight_blind_spot_forces_abstention():
    """A deck that covers everything except a high-weight section must abstain,
    even if raw coverage is high (the spec's 'skips a high-weight section' trap)."""
    outline = load_outline()
    counts = {}
    for s in outline.sections:
        if s.id == "bio_biochem":
            continue  # leave the highest-weight section completely uncovered
        for t in s.topics:
            counts[t] = 5
    report = compute_coverage(counts, outline)
    assert "bio_biochem" in report.missing_high_weight_sections
    assert report.abstains()
    # bio_biochem section coverage is zero
    bio = next(s for s in report.sections if s.id == "bio_biochem")
    assert bio.fraction == 0.0


def test_partial_coverage_threshold():
    outline = load_outline()
    # cover roughly the first third of every section
    counts = {}
    for s in outline.sections:
        for t in s.topics[: max(1, len(s.topics) // 3)]:
            counts[t] = 2
    report = compute_coverage(counts, outline)
    assert 0.0 < report.weighted_coverage < 1.0
    # with low coverage we abstain under the default threshold
    if report.weighted_coverage < DEFAULT_MIN_COVERAGE:
        assert report.abstains()
