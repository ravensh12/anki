# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for Dream Seed (nightly reel) and the Documentary (exam-eve montage)."""

from ante import documentary, dreamseed
from ante.config import AnteConfig


def _event(cid, correct, ms, front="Q", back="A", topic="mcat::bio_biochem::enzymes"):
    return {
        "card_id": cid, "correct": correct, "elapsed_ms": ms,
        "front": front, "back": back, "topic": topic,
    }


def test_reel_unavailable_without_events():
    reel = dreamseed.build_reel([])
    assert reel["available"] is False


def test_reel_prioritizes_struggled_misses():
    cfg = AnteConfig(dreamseed_scenes=3)
    events = [
        _event(1, True, 2000),          # fluent hit — low priority
        _event(2, False, 1000),         # careless miss
        _event(3, False, 9000),         # struggled miss — highest
    ]
    reel = dreamseed.build_reel(events, cfg=cfg)
    assert reel["available"]
    assert reel["scenes"][0]["topic"]  # ordered, struggled first
    # the struggled miss (card 3) should lead
    assert reel["scenes"][0]["front"] == "Q"
    order = [s for s in reel["scenes"]]
    assert len(order) <= 3


def test_reel_limits_scene_count():
    cfg = AnteConfig(dreamseed_scenes=2)
    events = [_event(i, False, 9000) for i in range(10)]
    reel = dreamseed.build_reel(events, cfg=cfg)
    assert len(reel["scenes"]) == 2


def test_reel_uses_palace_asset_when_present():
    cfg = AnteConfig(dreamseed_scenes=1)
    palace_by_card = {1: {"still": "still_x.svg", "motion": None, "caption": "cap"}}
    reel = dreamseed.build_reel([_event(1, False, 9000)], palace_by_card, cfg=cfg)
    assert reel["scenes"][0]["still"] == "still_x.svg"
    assert reel["scenes"][0]["caption"] == "cap"


def test_reel_narration_texts_include_closing():
    cfg = AnteConfig(dreamseed_scenes=2)
    reel = dreamseed.build_reel([_event(1, False, 9000), _event(2, False, 8000)], cfg=cfg)
    texts = dreamseed.narration_texts(reel)
    assert len(texts) == 3  # 2 scenes + closing
    assert texts[-1] == dreamseed.CLOSING_LINE


def test_documentary_gated_until_exam_eve():
    doc = documentary.build_documentary(
        exam_days_left=40, diagnostic={}, readiness={}, streak={},
        n_reviews=500, active_days=30, topics_mastered=10,
    )
    assert doc["available"] is False


def test_documentary_available_on_exam_eve():
    doc = documentary.build_documentary(
        exam_days_left=1, diagnostic={"taken": True}, readiness={"abstained": True},
        streak={"best": 12}, n_reviews=500, active_days=30, topics_mastered=10,
        baseline_total=498,
    )
    assert doc["available"] is True
    ids = [c["id"] for c in doc["chapters"]]
    assert ids == ["baseline", "work", "seals", "verdict", "sendoff"]


def test_documentary_verdict_respects_abstention():
    doc = documentary.build_documentary(
        exam_days_left=0, diagnostic={"taken": True}, readiness={"abstained": True},
        streak={}, n_reviews=600, active_days=40, topics_mastered=20, force=True,
    )
    verdict = next(c for c in doc["chapters"] if c["id"] == "verdict")
    assert "not enough evidence" in verdict["line"].lower() or "no number" in verdict["line"].lower()


def test_documentary_verdict_shows_earned_range():
    doc = documentary.build_documentary(
        exam_days_left=0, diagnostic={"taken": True},
        readiness={"abstained": False, "projected_total": 510, "total_range": [505, 515]},
        streak={}, n_reviews=600, active_days=40, topics_mastered=20,
    )
    verdict = next(c for c in doc["chapters"] if c["id"] == "verdict")
    assert "505" in verdict["line"] and "515" in verdict["line"]


def test_documentary_includes_palace_chapter_when_scenes_exist():
    doc = documentary.build_documentary(
        exam_days_left=1, diagnostic={"taken": True}, readiness={"abstained": True},
        streak={}, n_reviews=500, active_days=30, topics_mastered=10,
        palace_records=[{"still": "s.svg", "created_at": 5, "caption": "c"}],
    )
    assert any(c["id"] == "palace" for c in doc["chapters"])


def test_documentary_force_previews_early():
    doc = documentary.build_documentary(
        exam_days_left=99, diagnostic={"taken": True}, readiness={}, streak={},
        n_reviews=10, active_days=3, topics_mastered=1, force=True,
    )
    assert doc["available"] is True
