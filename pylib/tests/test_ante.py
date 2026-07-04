# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Integration tests for the Ante engine changes, exercised from Python.

Covers the two Rust additions:
  * the GetTopicMastery backend RPC, and
  * the points-at-stake review order (REVIEW_CARD_ORDER_POINTS_AT_STAKE).
"""

from anki.consts import QUEUE_TYPE_REV
from tests.shared import getEmptyCol


def _add_card(col, topic: str) -> int:
    note = col.newNote()
    note["Front"] = "q"
    note["Back"] = "a"
    if topic:
        note.tags = [topic]
    col.addNote(note)
    return note.cards()[0].id


def test_get_topic_mastery_groups_by_tag():
    col = getEmptyCol()
    _add_card(col, "mcat::bio_biochem::amino_acids")
    _add_card(col, "mcat::bio_biochem::enzymes")
    _add_card(col, "mcat::cars")
    _add_card(col, "")  # untagged

    resp = col._backend.get_topic_mastery(
        search="", topic_prefix="", mastery_threshold=0.0
    )

    topics = {t.topic: t for t in resp.topics}
    assert resp.total_cards == 4
    # two bio leaf topics + cars + untagged bucket
    assert resp.topic_count == 4
    assert topics["mcat::bio_biochem::amino_acids"].weight == 1.30
    assert topics["mcat::cars"].weight == 0.80
    assert topics["mcat::bio_biochem::amino_acids"].total_cards == 1
    # nothing reviewed yet -> no coverage
    assert topics["mcat::bio_biochem::amino_acids"].studied_cards == 0
    assert topics["mcat::bio_biochem::amino_acids"].coverage == 0.0
    # highest-weight topic is returned first
    assert resp.topics[0].weight >= resp.topics[-1].weight


def test_points_at_stake_review_order():
    col = getEmptyCol()
    weight_by_cid: dict[int, float] = {}
    # three low-value (cars, 0.80) and three high-value (bio, 1.30) cards
    for topic, weight in [
        ("mcat::cars", 0.80),
        ("mcat::bio_biochem::a", 1.30),
        ("mcat::cars", 0.80),
        ("mcat::bio_biochem::b", 1.30),
        ("mcat::cars", 0.80),
        ("mcat::bio_biochem::c", 1.30),
    ]:
        cid = _add_card(col, topic)
        card = col.get_card(cid)
        card.queue = QUEUE_TYPE_REV
        card.type = 2  # review
        card.due = 0
        card.ivl = 10
        col.update_card(card)
        weight_by_cid[cid] = weight

    # set the Default deck's config to the points-at-stake review order
    conf = col.decks.config_dict_for_deck_id(1)
    # REVIEW_CARD_ORDER_POINTS_AT_STAKE = 13
    conf["reviewOrder"] = 13
    col.decks.update_config(conf)

    col.reset()
    ordered_weights = []
    while True:
        card = col.sched.getCard()
        if not card:
            break
        ordered_weights.append(weight_by_cid[card.id])
        col.sched.answerCard(card, 3)

    # all high-value cards should be presented before any low-value ones
    assert ordered_weights == sorted(ordered_weights, reverse=True)
    assert ordered_weights[:3] == [1.30, 1.30, 1.30]
    assert ordered_weights[3:] == [0.80, 0.80, 0.80]


def test_points_at_stake_undo_and_no_corruption():
    """The points-at-stake order only changes presentation order, so answering
    must remain fully undoable and must not corrupt the collection."""
    col = getEmptyCol()
    for topic in ["mcat::cars", "mcat::bio_biochem::a", "mcat::psych_soc::b"]:
        cid = _add_card(col, topic)
        card = col.get_card(cid)
        card.queue = QUEUE_TYPE_REV
        card.type = 2
        card.due = 0
        card.ivl = 10
        col.update_card(card)

    conf = col.decks.config_dict_for_deck_id(1)
    conf["reviewOrder"] = 13  # REVIEW_CARD_ORDER_POINTS_AT_STAKE
    col.decks.update_config(conf)

    col.reset()
    card = col.sched.getCard()
    assert card is not None
    due_before = col.get_card(card.id).due
    col.sched.answerCard(card, 3)
    assert col.get_card(card.id).due != due_before

    # undo restores the card
    assert col.undo_status().undo != ""
    col.undo()
    assert col.get_card(card.id).due == due_before

    # collection passes an integrity check
    _problems, ok = col.fix_integrity()
    assert ok
