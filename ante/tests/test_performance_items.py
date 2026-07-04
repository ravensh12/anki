# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for application/transfer items gating mastery (the reviewer's push)."""

from ante.mastery import MasteryStatus, TopicStats, compute_mastery
from ante.outline import load_outline
from ante.performance_items import (
    REASSESS_AFTER_DAYS,
    SECONDS_PER_DAY,
    due_items,
    is_correct,
    items_by_topic,
    load_items,
    next_item,
    normalize_log,
    paraphrase_gaps,
    quiz_progress,
    topic_application_accuracy,
)


def test_items_load_and_are_application_style():
    items = load_items()
    assert len(items) >= 12
    # every item is multiple choice with a valid correct index
    for it in items:
        assert len(it.choices) >= 2
        assert 0 <= it.correct_index < len(it.choices)
        assert it.topic.startswith("mcat::")


def test_grading():
    aa = items_by_topic()["mcat::bio_biochem::amino_acids"][0]
    assert is_correct(aa.id, aa.correct_index)
    assert not is_correct(aa.id, (aa.correct_index + 1) % len(aa.choices))


def test_topic_accuracy_from_responses():
    items = items_by_topic()["mcat::bio_biochem::enzymes"]
    # answer both enzyme items, one right one wrong
    responses = {
        items[0].id: items[0].correct_index,
        items[1].id: (items[1].correct_index + 1) % len(items[1].choices),
    }
    acc = topic_application_accuracy(responses)
    point, lo, hi = acc["mcat::bio_biochem::enzymes"]
    assert abs(point - 0.5) < 1e-9
    assert lo <= point <= hi


def test_next_item_prefers_topic_and_skips_answered():
    responses = {}
    first = next_item(responses, prefer_topic="mcat::psych_soc::learning")
    assert first.topic == "mcat::psych_soc::learning"
    responses[first.id] = 0
    nxt = next_item(responses, prefer_topic="mcat::psych_soc::learning")
    # learning has one item; once answered it should move on
    assert nxt is None or nxt.id != first.id


def test_mastery_requires_application_not_just_recall():
    """A topic strong on recall but with FAILED application items must NOT reach
    mastered \u2014 this is the whole point of the reviewer's critique."""
    outline = load_outline()
    aa = "mcat::bio_biochem::amino_acids"
    items = items_by_topic()[aa]
    # strong recall
    stats_recall_only = {aa: TopicStats(aa, 10, 10, 0.97, perf_accuracy=None)}
    assert (
        compute_mastery(stats_recall_only, outline)[aa].status != MasteryStatus.MASTERED
    )

    # strong recall but they FAIL the application items -> not mastered
    failed = {it.id: (it.correct_index + 1) % len(it.choices) for it in items}
    app = topic_application_accuracy(failed)  # 0% on amino_acids
    stats_failed_app = {aa: TopicStats(aa, 10, 10, 0.97, perf_accuracy=app[aa][0])}
    assert (
        compute_mastery(stats_failed_app, outline)[aa].status
        == MasteryStatus.CORRECTIVE
    )

    # strong recall AND they pass application -> mastered
    passed = {it.id: it.correct_index for it in items}
    app2 = topic_application_accuracy(passed)  # 100%
    stats_ok = {aa: TopicStats(aa, 10, 10, 0.97, perf_accuracy=app2[aa][0])}
    assert compute_mastery(stats_ok, outline)[aa].status == MasteryStatus.MASTERED


def test_paraphrase_gap_flags_memorization():
    aa = "mcat::bio_biochem::amino_acids"
    items = items_by_topic()[aa]
    failed = {it.id: (it.correct_index + 1) % len(it.choices) for it in items}
    rows = paraphrase_gaps(failed, {aa: 0.95})  # recall high, application 0
    assert rows and rows[0].gap > 0.5  # big memory>application gap


# --- Bloom re-test loop (attempt log, re-queue, spaced re-assessment) --------


def test_normalize_log_accepts_legacy_and_new_shapes():
    it = load_items()[0]
    legacy = normalize_log({it.id: 1})  # old one-shot {id: choice}
    assert legacy[it.id][0].choice == 1
    new = normalize_log({it.id: [[2, 5.0], [3, 9.0]]})  # attempt log
    assert [a.choice for a in new[it.id]] == [2, 3]
    assert new[it.id][-1].ts == 9.0


def test_accuracy_uses_most_recent_attempt():
    enz = "mcat::bio_biochem::enzymes"
    it = items_by_topic()[enz][0]
    wrong = (it.correct_index + 1) % len(it.choices)
    # last answer wrong -> 0%
    assert topic_application_accuracy({it.id: [[wrong, 100.0]]})[enz][0] == 0.0
    # a later correct re-test flips it to 100% (gate can reopen)
    fixed = {it.id: [[wrong, 100.0], [it.correct_index, 200.0]]}
    assert topic_application_accuracy(fixed)[enz][0] == 1.0


def test_wrong_answer_is_requeued_correct_is_not():
    it = items_by_topic()["mcat::bio_biochem::enzymes"][0]
    wrong = (it.correct_index + 1) % len(it.choices)
    assert it.id in [d.id for d in due_items({it.id: [[wrong, 100.0]]})]
    # a fresh correct answer is not due
    fresh = {it.id: [[it.correct_index, 100.0]]}
    assert it.id not in [d.id for d in due_items(fresh, now=101.0)]


def test_mastery_lapses_on_failed_retest():
    mem = "mcat::psych_soc::memory"
    items = items_by_topic()[mem]
    good = {it.id: [[it.correct_index, 10.0]] for it in items}
    assert topic_application_accuracy(good)[mem][0] == 1.0
    # a later failed re-test on one item drops the topic below 100% (mastery lapses)
    lapsed = dict(good)
    bad = items[0]
    lapsed[bad.id] = [
        [bad.correct_index, 10.0],
        [(bad.correct_index + 1) % len(bad.choices), 20.0],
    ]
    expected = (len(items) - 1) / len(items)
    assert expected < 1.0
    assert topic_application_accuracy(lapsed)[mem][0] == expected


def test_correct_item_becomes_due_after_reassessment_window():
    it = items_by_topic()["mcat::psych_soc::memory"][0]
    ts = 1000.0
    resp = {it.id: [[it.correct_index, ts]]}
    assert it.id not in [d.id for d in due_items(resp, now=ts + 1)]
    later = ts + REASSESS_AFTER_DAYS * SECONDS_PER_DAY + 1
    assert it.id in [d.id for d in due_items(resp, now=later)]


def test_quiz_progress_counts_and_reassess_countdown():
    total = len(load_items())
    empty = quiz_progress({}, now=1000.0)
    assert empty["total"] == total
    assert empty["attempted"] == 0 and empty["proven"] == 0
    assert empty["due"] == total
    it = load_items()[0]
    prog = quiz_progress({it.id: [[it.correct_index, 1000.0]]}, now=1000.0)
    assert prog["attempted"] == 1 and prog["proven"] == 1
    assert prog["due"] == total - 1
    assert prog["next_reassess_days"] is not None
    assert round(prog["next_reassess_days"]) == round(REASSESS_AFTER_DAYS)
