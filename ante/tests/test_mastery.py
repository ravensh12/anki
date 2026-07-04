# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the mastery-gating engine (PRD Section 6)."""

from dataclasses import replace

from ante.config import CONFIG
from ante.mastery import (
    MasteryStatus,
    TopicStats,
    compute_mastery,
    mastery_map,
    next_unlockable,
)
from ante.outline import load_outline

OUTLINE = load_outline()


def _stats(tag, total=10, at_strength=0, recall=0.5, perf=None):
    return TopicStats(
        tag=tag,
        cards_total=total,
        cards_at_strength=at_strength,
        average_recall=recall,
        perf_accuracy=perf,
    )


def test_topic_with_no_prereqs_starts_active_not_locked():
    # amino_acids has no prereqs
    stats = {"mcat::bio_biochem::amino_acids": _stats("mcat::bio_biochem::amino_acids")}
    m = compute_mastery(stats, OUTLINE)
    assert m["mcat::bio_biochem::amino_acids"].status == MasteryStatus.ACTIVE


def test_topic_locks_until_prereqs_mastered():
    # enzymes requires protein_structure requires amino_acids
    enzymes = "mcat::bio_biochem::enzymes"
    m = compute_mastery({enzymes: _stats(enzymes)}, OUTLINE)
    assert m[enzymes].status == MasteryStatus.LOCKED
    assert "mcat::bio_biochem::protein_structure" in m[enzymes].blocked_by


def test_mastery_gates_on_application_only():
    aa = "mcat::bio_biochem::amino_acids"
    # strong recall but NO application evidence -> not mastered (honest)
    strong_only = {aa: _stats(aa, total=10, at_strength=10, recall=0.95, perf=None)}
    assert compute_mastery(strong_only, OUTLINE)[aa].status == MasteryStatus.ACTIVE

    # passing application masters the topic even with weak flashcard strength:
    # mastery is proven by USE, not by flashcard self-ratings.
    app_only = {aa: _stats(aa, total=10, at_strength=2, recall=0.5, perf=0.9)}
    assert compute_mastery(app_only, OUTLINE)[aa].status == MasteryStatus.MASTERED

    # failing application -> not mastered regardless of strength
    failed = {aa: _stats(aa, total=10, at_strength=10, recall=0.95, perf=0.4)}
    assert compute_mastery(failed, OUTLINE)[aa].status != MasteryStatus.MASTERED


def test_mastery_can_require_strength_when_configured():
    from dataclasses import replace

    aa = "mcat::bio_biochem::amino_acids"
    cfg = replace(CONFIG, mastery_requires_strength=True)
    # passing application but weak strength -> active when strength is required
    app_only = {aa: _stats(aa, total=10, at_strength=2, recall=0.5, perf=0.9)}
    assert compute_mastery(app_only, OUTLINE, cfg)[aa].status == MasteryStatus.ACTIVE
    # both -> mastered
    both = {aa: _stats(aa, total=10, at_strength=9, recall=0.95, perf=0.9)}
    assert compute_mastery(both, OUTLINE, cfg)[aa].status == MasteryStatus.MASTERED


def test_test_out_path_masters_on_application_alone():
    aa = "mcat::bio_biochem::amino_acids"
    cfg = replace(CONFIG, test_out_enabled=True)
    app_only = {aa: _stats(aa, total=10, at_strength=1, recall=0.4, perf=0.9)}
    m = compute_mastery(app_only, OUTLINE, cfg)
    assert m[aa].status == MasteryStatus.MASTERED


def test_unlock_propagates_through_chain():
    aa = "mcat::bio_biochem::amino_acids"
    ps = "mcat::bio_biochem::protein_structure"
    enz = "mcat::bio_biochem::enzymes"
    # master amino_acids + protein_structure -> enzymes should unlock to active
    stats = {
        aa: _stats(aa, 10, 10, 0.95, 0.9),
        ps: _stats(ps, 10, 10, 0.95, 0.9),
        enz: _stats(enz, 10, 1, 0.4, None),
    }
    m = compute_mastery(stats, OUTLINE)
    assert m[aa].status == MasteryStatus.MASTERED
    assert m[ps].status == MasteryStatus.MASTERED
    assert m[enz].status == MasteryStatus.ACTIVE


def test_corrective_routing_when_application_dips():
    aa = "mcat::bio_biochem::amino_acids"
    # unlocked, has cards, but application below the corrective bar
    stats = {aa: _stats(aa, total=10, at_strength=8, recall=0.8, perf=0.4)}
    m = compute_mastery(stats, OUTLINE)
    assert m[aa].status == MasteryStatus.CORRECTIVE
    # corrective topics get boosted weakness so they surface first
    assert m[aa].weakness > (1.0 - m[aa].normalized_mastery) - 1e-9


def test_weakness_drives_ordering():
    aa = "mcat::bio_biochem::amino_acids"
    # weak topic should have high weakness
    weak = compute_mastery({aa: _stats(aa, 10, 1, 0.3, 0.4)}, OUTLINE)[aa]
    strong = compute_mastery({aa: _stats(aa, 10, 9, 0.9, 0.85)}, OUTLINE)[aa]
    assert weak.weakness > strong.weakness


def test_mastery_map_rollup_counts_states():
    aa = "mcat::bio_biochem::amino_acids"
    stats = {aa: _stats(aa, 10, 10, 0.95, 0.9)}
    m = compute_mastery(stats, OUTLINE)
    rollup = mastery_map(m, OUTLINE)
    assert rollup["counts"]["mastered"] >= 1
    bio = next(s for s in rollup["sections"] if s["id"] == "bio_biochem")
    assert bio["counts"]["mastered"] >= 1
    # most topics with no data are locked or active, never "covered = progress"
    assert set(bio["counts"]) == {"locked", "active", "corrective", "mastered"}


def test_next_unlockable_prefers_fewest_blockers():
    m = compute_mastery({}, OUTLINE)  # nothing studied
    order = next_unlockable(m)
    # topics with prereqs are locked; the first should have the fewest blockers
    assert order
    first = m[order[0]]
    assert first.status == MasteryStatus.LOCKED
