# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for open-ended items (offline grading + Bloom loop), the combined
application signal, and response-time analytics."""

from ante.analytics import (
    CARELESS,
    EFFORTFUL,
    FLUENT,
    STRUGGLED,
    classify_response,
    timing_summary,
)
from ante.applied import combined_topic_performance
from ante.openended import (
    grade_open_answer,
    load_open_items,
    open_items_by_topic,
    open_progress,
    topic_open_accuracy,
    topic_open_counts,
)
from ante.performance_items import items_by_topic


def _an_open_item():
    return load_open_items()[0]


def test_open_items_load_for_every_topic():
    items = load_open_items()
    assert len(items) >= 36
    for it in items[:20]:
        assert it.topic.startswith("mcat::")
        assert it.prompt and it.model_answer


def test_grading_rewards_model_answer_and_rejects_nonsense():
    from ante.config import CONFIG

    it = _an_open_item()
    good = grade_open_answer(it.model_answer, it)
    bad = grade_open_answer("no idea, something something cells maybe", it)
    # a fuzzy offline grader: the model answer must clearly PASS and clearly beat
    # nonsense (rubric points are paraphrased, so we don't require a perfect 1.0).
    assert good.score >= CONFIG.open_pass_score
    assert good.score >= bad.score + 0.3


def test_grading_brevity_guard_caps_one_word_answers():
    it = _an_open_item()
    # dumping a single keyword should not pass
    kw = it.keywords[0] if it.keywords else "enzyme"
    assert grade_open_answer(kw, it).score <= 0.5


def test_open_counts_use_latest_attempt():
    it = _an_open_item()
    # first attempt poor, re-attempt strong -> latest wins
    resp = {it.id: [[0.2, 10.0], [0.9, 20.0]]}
    counts = topic_open_counts(resp)
    assert counts[it.topic] == (0.9, 1)
    acc = topic_open_accuracy(resp)
    assert acc[it.topic][0] == 0.9


def test_open_progress_counts_due_and_proven():
    it = _an_open_item()
    empty = open_progress({}, now=1000.0)
    assert empty["due"] == empty["total"] and empty["proven"] == 0
    prog = open_progress({it.id: [[0.9, 1000.0]]}, now=1000.0)
    assert prog["attempted"] == 1 and prog["proven"] == 1


def test_combined_performance_pools_mcq_and_open():
    topic = "mcat::bio_biochem::enzymes"
    mcq = items_by_topic()[topic]
    open_items = open_items_by_topic()[topic]
    # 1 MCQ correct, 1 open scored 1.0 -> pooled accuracy 1.0 over 2 items
    mcq_resp = {mcq[0].id: [[mcq[0].correct_index, 5.0]]}
    open_resp = {open_items[0].id: [[1.0, 6.0]]}
    combined = combined_topic_performance(mcq_resp, open_resp)
    point, lo, hi = combined[topic]
    assert point == 1.0
    assert lo <= point <= hi


def test_response_time_classification():
    assert classify_response(True, 3000) == FLUENT
    assert classify_response(True, 20000) == EFFORTFUL
    assert classify_response(False, 1500) == CARELESS
    assert classify_response(False, 30000) == STRUGGLED
    assert classify_response(True, None) == "unknown"


def test_timing_summary_flags_careless_misses():
    events = [(False, 1000)] * 6 + [(True, 3000)] * 4  # mostly fast wrongs
    rep = timing_summary(events)
    assert rep["available"]
    assert rep["counts"][CARELESS] == 6
    assert "slow down" in rep["insight"].lower()
