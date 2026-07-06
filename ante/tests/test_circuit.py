# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the Circuit world model — the card den that replaced the dashboard."""

from ante.circuit import build_world
from ante.mastery import MasteryStatus, TopicMastery
from ante.outline import load_outline


def _tm(
    tag: str, status: MasteryStatus, *, cards=8, recall=0.8, weight=0.3
) -> TopicMastery:
    return TopicMastery(
        tag=tag,
        name=tag.rsplit("::", 1)[-1],
        section_id=tag.split("::")[1] if "::" in tag[6:] else tag.split("::")[-1],
        status=status,
        exam_weight=weight,
        cards_total=cards,
        cards_at_strength=int(cards * 0.5),
        strength_fraction=0.5,
        average_recall=recall,
        perf_accuracy=0.7,
        normalized_mastery=0.6,
        weakness=0.4,
        reasons=("test",),
    )


def _mastery(**states) -> dict[str, TopicMastery]:
    outline = load_outline()
    out = {}
    for t in outline.all_topic_objs():
        st = states.get(t.tag, MasteryStatus.ACTIVE)
        cards = 0 if st == "unlisted" else 8
        status = MasteryStatus.ACTIVE if st == "unlisted" else st
        out[t.tag] = TopicMastery(
            tag=t.tag,
            name=t.name,
            section_id=t.section_id,
            status=status,
            exam_weight=t.exam_weight,
            cards_total=cards,
            cards_at_strength=cards // 2,
            strength_fraction=0.5 if cards else 0.0,
            average_recall=0.8 if cards else 0.0,
            perf_accuracy=0.7 if cards else None,
            normalized_mastery=0.6 if cards else 0.0,
            weakness=0.4,
            reasons=(),
        )
    return out


def test_table_states_map_one_to_one():
    tag_won = "mcat::bio_biochem::enzymes"
    tag_low = "mcat::chem_phys::thermodynamics"
    m = _mastery(**{tag_won: MasteryStatus.MASTERED, tag_low: MasteryStatus.CORRECTIVE})
    w = build_world(m)
    tables = {t["tag"]: t for c in w["cities"] for t in c["tables"]}
    assert tables[tag_won]["state"] == "won"
    assert tables[tag_low]["state"] == "lowtable"
    assert w["counts"]["won"] >= 1
    assert w["counts"]["lowtable"] >= 1


def test_no_evidence_is_unlisted_not_pretended():
    tag = "mcat::psych_soc::memory"
    m = _mastery(**{tag: "unlisted"})
    w = build_world(m)
    tables = {t["tag"]: t for c in w["cities"] for t in c["tables"]}
    assert tables[tag]["state"] == "unlisted"
    assert tables[tag]["dust"] is None  # no decay claim without evidence


def test_dust_thickens_as_recall_fades():
    m = _mastery()
    tag = next(iter(m))
    weak = dict(m)
    strong = dict(m)
    weak[tag] = _tm(tag, MasteryStatus.MASTERED, recall=0.3)
    strong[tag] = _tm(tag, MasteryStatus.MASTERED, recall=0.95)
    dust_weak = {t["tag"]: t for c in build_world(weak)["cities"] for t in c["tables"]}[
        tag
    ]["dust"]
    dust_strong = {
        t["tag"]: t for c in build_world(strong)["cities"] for t in c["tables"]
    }[tag]["dust"]
    assert dust_weak > dust_strong


def test_cities_cover_all_sections_with_flavor():
    w = build_world(_mastery())
    outline = load_outline()
    assert len(w["cities"]) == len(outline.sections)
    assert {c["id"] for c in w["cities"]} == {s.id for s in outline.sections}
    # every city has a name and a room
    assert all(c["city"] and c["room"] for c in w["cities"])


def test_seat_buyin_before_diagnostic():
    w = build_world(_mastery(), diagnostic_taken=False, due_count=40)
    assert w["seat"]["kind"] == "buyin"


def test_seat_keeps_the_bookend_session():
    ritual = {
        "next": "first_light",
        "morning": {"done": False},
        "night": {"done": False},
    }
    tag = "mcat::bio_biochem::enzymes"
    w = build_world(
        _mastery(),
        ritual=ritual,
        due_count=12,
        best_next_topic=tag,
        diagnostic_taken=True,
    )
    assert w["seat"]["kind"] == "session"
    assert w["seat"]["table"] == tag
    assert "morning game" in w["seat"]["label"]


def test_seat_premiere_outranks_headsup():
    w = build_world(
        _mastery(),
        due_count=0,
        documentary_ready=True,
        viva_suggested=[{"topic": "mcat::cars", "name": "CARS"}],
        diagnostic_taken=True,
    )
    assert w["seat"]["kind"] == "premiere"


def test_seat_headsup_when_a_topic_is_close():
    w = build_world(
        _mastery(),
        due_count=0,
        viva_suggested=[{"topic": "mcat::cars", "name": "CARS"}],
        diagnostic_taken=True,
    )
    assert w["seat"]["kind"] == "headsup"
    assert w["seat"]["topic"] == "mcat::cars"


def test_due_bookend_game_outranks_headsup_even_with_only_new_cards():
    # a fresh deck: no review-due cards, but new cards to play, and the midnight
    # game hasn't been kept yet. The daily game must take the seat, not Sahir.
    ritual = {"next": "last_light", "morning": {"done": True}, "night": {"done": False}}
    tag = "mcat::bio_biochem::amino_acids"
    w = build_world(
        _mastery(),
        ritual=ritual,
        due_count=0,
        new_count=55,
        best_next_topic=tag,
        viva_suggested=[{"topic": "mcat::cars", "name": "CARS"}],
        diagnostic_taken=True,
    )
    assert w["seat"]["kind"] == "session"
    assert w["seat"]["table"] == tag
    assert "midnight game" in w["seat"]["label"]


def test_headsup_returns_once_both_games_are_kept():
    # both bookends done -> ritual has no "next" -> Sahir can take the seat
    ritual = {"next": None, "morning": {"done": True}, "night": {"done": True}}
    w = build_world(
        _mastery(),
        ritual=ritual,
        due_count=0,
        new_count=55,
        viva_suggested=[{"topic": "mcat::cars", "name": "CARS"}],
        diagnostic_taken=True,
    )
    assert w["seat"]["kind"] == "headsup"
    assert w["seat"]["topic"] == "mcat::cars"


def test_missed_morning_after_night_kept_frees_headsup():
    # night kept, morning slipped (ritual points at tomorrow's first light):
    # today's owed game is resolved, so heads-up is allowed again.
    ritual = {
        "next": "first_light",
        "morning": {"done": False},
        "night": {"done": True},
    }
    w = build_world(
        _mastery(),
        ritual=ritual,
        due_count=0,
        new_count=55,
        viva_suggested=[{"topic": "mcat::cars", "name": "CARS"}],
        diagnostic_taken=True,
    )
    assert w["seat"]["kind"] == "headsup"


def test_seat_reel_at_night_then_book():
    w = build_world(
        _mastery(),
        due_count=0,
        dreamseed_ready=True,
        now_hour=21,
        diagnostic_taken=True,
    )
    assert w["seat"]["kind"] == "reel"
    w2 = build_world(
        _mastery(),
        due_count=0,
        dreamseed_ready=False,
        now_hour=21,
        diagnostic_taken=True,
    )
    assert w2["seat"]["kind"] == "book"


def test_night_phase_follows_the_ritual():
    dawn = build_world(_mastery(), now_hour=8, ritual={"morning": {"done": False}})
    day = build_world(_mastery(), now_hour=8, ritual={"morning": {"done": True}})
    assert dawn["night"]["phase"] == "dawn"
    assert day["night"]["phase"] == "day"


def test_chip_stacks_scale_with_stake():
    w = build_world(_mastery())
    chips = [t["chips"] for c in w["cities"] for t in c["tables"]]
    assert all(1 <= n <= 5 for n in chips)
    assert max(chips) == 5  # the biggest table carries the full stack
