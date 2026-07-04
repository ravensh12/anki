# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the Viva — oral test-out state machine over the rubric grader."""

import pytest

from ante import viva
from ante.config import AnteConfig
from ante.mastery import MasteryStatus, TopicMastery
from ante.openended import load_open_items

# a topic that exists in the bundled open-ended bank
TOPIC = "mcat::bio_biochem::amino_acids"


@pytest.fixture(autouse=True)
def _require_bank():
    if not any(it.topic == TOPIC for it in load_open_items()):
        pytest.skip("open-ended bank missing the fixture topic")


def _tm(tag, status, perf=0.7):
    return TopicMastery(
        tag=tag, name=tag.rsplit("::", 1)[-1], section_id="bio_biochem",
        status=status, exam_weight=1.0, cards_total=10, cards_at_strength=6,
        strength_fraction=0.6, average_recall=0.8, perf_accuracy=perf,
        normalized_mastery=0.7, weakness=0.3,
    )


def _model_answer_for(session):
    item = viva._item_of(session)
    return item.model_answer


def test_eligible_lists_active_and_corrective_only():
    mastery = {
        TOPIC: _tm(TOPIC, MasteryStatus.ACTIVE, 0.72),
        "mcat::bio_biochem::locked": _tm("mcat::bio_biochem::locked", MasteryStatus.LOCKED),
    }
    elig = viva.eligible_topics(mastery)
    tags = {e["topic"] for e in elig}
    assert TOPIC in tags
    assert "mcat::bio_biochem::locked" not in tags


def test_eligible_orders_closest_to_bar_first():
    mastery = {
        TOPIC: _tm(TOPIC, MasteryStatus.ACTIVE, 0.78),
    }
    elig = viva.eligible_topics(mastery)
    assert elig[0]["topic"] == TOPIC
    assert elig[0]["gap"] == pytest.approx(0.02, abs=1e-6)


def test_start_viva_builds_session():
    s = viva.start_viva(TOPIC)
    assert s is not None
    assert s["status"] == viva.OPEN_STATUS
    assert s["question"]
    assert s["topic_name"]
    assert s["rounds"] == []


def test_start_viva_unknown_topic_returns_none():
    assert viva.start_viva("mcat::nonexistent::topic") is None


def test_strong_answer_passes_and_produces_log_record():
    cfg = AnteConfig(viva_pass_score=0.5, viva_probe_rounds=1)
    s = viva.start_viva(TOPIC, cfg=cfg)
    s = viva.submit_answer(s, _model_answer_for(s), now=1000.0, cfg=cfg)
    # a model answer should clear a modest bar, possibly after auto-closing
    while s["status"] == viva.OPEN_STATUS:
        s = viva.submit_answer(s, _model_answer_for(s), now=1000.0, cfg=cfg)
    assert s["status"] == viva.PASSED
    assert s["verdict"]["passed"] is True
    records = viva.records_for_log(s)
    assert records and records[0][0] == s["item_id"]
    assert records[0][1] >= 0.5


def test_empty_answers_fail_after_probes():
    cfg = AnteConfig(viva_pass_score=0.75, viva_probe_rounds=2)
    s = viva.start_viva(TOPIC, cfg=cfg)
    for _ in range(5):
        if s["status"] != viva.OPEN_STATUS:
            break
        s = viva.submit_answer(s, "I don't know", now=1000.0, cfg=cfg)
    assert s["status"] == viva.FAILED
    assert s["verdict"]["passed"] is False
    assert "missing" in s["verdict"]


def test_probe_targets_missing_rubric_point():
    cfg = AnteConfig(viva_pass_score=0.99, viva_probe_rounds=2)
    s = viva.start_viva(TOPIC, cfg=cfg)
    s = viva.submit_answer(s, "Something vague and short", now=1000.0, cfg=cfg)
    # still open -> an examiner probe was produced
    if s["status"] == viva.OPEN_STATUS:
        assert s.get("ask")


def test_probe_rounds_are_bounded():
    cfg = AnteConfig(viva_pass_score=0.99, viva_probe_rounds=2)
    s = viva.start_viva(TOPIC, cfg=cfg)
    n = 0
    while s["status"] == viva.OPEN_STATUS and n < 10:
        s = viva.submit_answer(s, "partial", now=1000.0, cfg=cfg)
        n += 1
    assert s["status"] in (viva.PASSED, viva.FAILED)
    assert len(s["rounds"]) <= 1 + cfg.viva_probe_rounds


def test_already_examined_today_gates_retries():
    log = [{"topic": TOPIC, "finished_at": 10_000.0}]
    assert viva.already_examined_today(log, TOPIC, now=10_500.0) is True
    assert viva.already_examined_today(log, TOPIC, now=10_000.0 + 90_000) is False


def test_llm_probe_used_when_available():
    cfg = AnteConfig(viva_pass_score=0.99, viva_probe_rounds=1)

    class Provider:
        def complete(self, system, user, max_tokens=80):
            return "What drives that process at the molecular level?"

    s = viva.start_viva(TOPIC, cfg=cfg)
    s = viva.submit_answer(s, "vague", provider=Provider(), now=1.0, cfg=cfg)
    if s["status"] == viva.OPEN_STATUS:
        assert "molecular level" in s["ask"]
