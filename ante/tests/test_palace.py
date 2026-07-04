# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the Palace — leech selection, spec verification, offline commission."""

import pytest

from ante import palace
from ante.ai.studio import Studio
from ante.config import AnteConfig
from ante.palace import Leech


def _card(cid, front, back, lapses, r=0.3, topic="mcat::bio_biochem::enzymes"):
    return {
        "card_id": cid,
        "front": front,
        "back": back,
        "lapses": lapses,
        "retrievability": r,
        "topic": topic,
    }


def test_pick_leeches_respects_min_lapses():
    cfg = AnteConfig(palace_min_lapses=3)
    cards = [_card(1, "Q1", "A1", 2), _card(2, "Q2", "A2", 3), _card(3, "Q3", "A3", 9)]
    picked = palace.pick_leeches(cards, cfg=cfg)
    assert {l.card_id for l in picked} == {2, 3}


def test_pick_leeches_orders_by_severity():
    cfg = AnteConfig(palace_min_lapses=1)
    cards = [_card(1, "Q1", "A1", 3, r=0.9), _card(2, "Q2", "A2", 8, r=0.1)]
    picked = palace.pick_leeches(cards, cfg=cfg)
    assert picked[0].card_id == 2  # more lapses, lower retrievability


def test_pick_leeches_skips_existing():
    cfg = AnteConfig(palace_min_lapses=1)
    cards = [_card(1, "Q", "A", 5), _card(2, "Q", "A", 5)]
    picked = palace.pick_leeches(cards, existing_card_ids={1}, cfg=cfg)
    assert [l.card_id for l in picked] == [2]


def test_pick_leeches_strips_html():
    cfg = AnteConfig(palace_min_lapses=1)
    picked = palace.pick_leeches([_card(1, "<b>Front</b>", "A<br>B", 5)], cfg=cfg)
    assert picked[0].front == "Front"
    assert "B" in picked[0].back and "<" not in picked[0].back


def test_offline_scene_spec_is_faithful_by_construction():
    leech = Leech(1, "What enzyme unwinds DNA?", "helicase unwinds the double helix",
                  "mcat::bio_biochem::enzymes", 5, 0.2)
    spec = palace.offline_scene_spec(leech)
    assert spec["anchors"]
    # every anchor fact must be a term drawn from the card itself
    verified = palace.verify_spec(spec, leech)
    assert len(verified["anchors"]) == len(spec["anchors"])


def test_verify_spec_drops_unsupported_anchors():
    leech = Leech(1, "What is the powerhouse of the cell?", "the mitochondrion",
                  "mcat::bio_biochem::cell", 4, 0.3)
    spec = {
        "title": "Cell",
        "scene": "a scene",
        "anchors": [
            {"fact": "mitochondrion", "object": "a brass furnace"},
            {"fact": "chloroplast photosynthesis in plants", "object": "a green lamp"},
        ],
    }
    verified = palace.verify_spec(spec, leech)
    facts = [a["fact"] for a in verified["anchors"]]
    assert "mitochondrion" in facts
    assert "chloroplast photosynthesis in plants" not in facts


def test_build_scene_spec_falls_back_when_llm_unsupported():
    # a provider that returns only unsupported anchors -> offline spec used
    class BadProvider:
        def complete(self, system, user, max_tokens=500):
            return '{"title":"x","scene":"y","anchors":[{"fact":"unrelated nonsense term","object":"z"}]}'

    leech = Leech(1, "What binds oxygen in blood?", "hemoglobin binds oxygen",
                  "mcat::bio_biochem::blood", 5, 0.2)
    spec = palace.build_scene_spec(leech, provider=BadProvider())
    assert spec["anchors"]  # fell back to offline, which is faithful
    assert palace.verify_spec(spec, leech)["anchors"]


def test_commission_offline_produces_record(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_KEY", raising=False)
    studio = Studio(tmp_path, force_offline=True)
    leech = Leech(42, "What enzyme unwinds DNA?", "helicase unwinds the helix",
                  "mcat::bio_biochem::enzymes", 6, 0.15)
    rec = palace.commission(leech, studio)
    assert rec["card_id"] == 42
    assert rec["still"] and rec["still"].endswith(".svg")
    assert rec["motion"] is None  # offline: no video
    assert rec["anchors"]


def test_gallery_payload_groups_by_topic():
    records = [
        {"card_id": 1, "topic": "mcat::bio_biochem::enzymes", "created_at": 1, "still": "a.svg"},
        {"card_id": 2, "topic": "mcat::bio_biochem::enzymes", "created_at": 2, "still": "b.svg"},
        {"card_id": 3, "topic": "mcat::chem_phys::acids", "created_at": 3, "still": "c.svg"},
    ]
    payload = palace.gallery_payload(records, pending=2)
    assert payload["count"] == 3
    assert payload["pending"] == 2
    assert len(payload["groups"]) == 2
    enzymes = next(g for g in payload["groups"] if g["topic"].endswith("enzymes"))
    assert enzymes["scenes"][0]["card_id"] == 2  # newest first


def test_index_by_card():
    records = [{"card_id": 5, "still": "x.svg"}, {"card_id": 6, "still": "y.svg"}]
    idx = palace.index_by_card(records)
    assert set(idx) == {5, 6}
