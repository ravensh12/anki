# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the literal paraphrase test (spec 7d): 30 cards x 2 questions."""

from ante.paraphrase import (
    load_cards,
    per_card_gaps,
    question_correct,
    summarize,
)


def test_set_is_thirty_cards_each_with_two_questions():
    cards = load_cards()
    assert len(cards) >= 30
    for c in cards:
        assert len(c.questions) == 2, f"{c.card_id} should have exactly 2 reworded questions"
        for q in c.questions:
            assert len(q.choices) >= 2
            assert 0 <= q.correct_index < len(q.choices)
        # the two questions must be distinct items
        assert c.questions[0].id != c.questions[1].id


def test_question_ids_are_globally_unique():
    ids = [q.id for c in load_cards() for q in c.questions]
    assert len(ids) == len(set(ids))


def test_grading_matches_correct_index():
    q = load_cards()[0].questions[0]
    assert question_correct(q.id, q.correct_index)
    assert not question_correct(q.id, (q.correct_index + 1) % len(q.choices))


def test_memorizer_shows_a_large_gap():
    """Full recall but wrong on the reworded questions -> big positive gap."""
    cards = load_cards()
    recall = {c.card_id: 1.0 for c in cards}
    # answer every reworded question WRONG
    wrong = {
        q.id: (q.correct_index + 1) % len(q.choices)
        for c in cards
        for q in c.questions
    }
    summary = summarize(recall, wrong)
    assert summary.n_cards >= 30
    assert summary.mean_reworded_accuracy == 0.0
    assert summary.gap > 0.9
    assert summary.meaningful


def test_transfer_learner_shows_no_gap():
    """Full recall AND correct on the reworded questions -> gap ~0 (the
    performance signal would just be copying memory here)."""
    cards = load_cards()
    recall = {c.card_id: 1.0 for c in cards}
    right = {q.id: q.correct_index for c in cards for q in c.questions}
    summary = summarize(recall, right)
    assert summary.mean_reworded_accuracy == 1.0
    assert abs(summary.gap) < 1e-9
    assert not summary.meaningful


def test_per_card_accuracy_is_fraction_of_answered():
    cards = load_cards()
    card = cards[0]
    recall = {card.card_id: 0.8}
    # one right, one wrong on this card's two questions
    answers = {
        card.questions[0].id: card.questions[0].correct_index,
        card.questions[1].id: (card.questions[1].correct_index + 1)
        % len(card.questions[1].choices),
    }
    rows = per_card_gaps(recall, answers)
    row = next(r for r in rows if r.card_id == card.card_id)
    assert row.n_questions == 2
    assert row.reworded_accuracy == 0.5
    assert abs(row.gap - (0.8 - 0.5)) < 1e-9


def test_cards_without_recall_or_answers_are_skipped():
    cards = load_cards()
    card = cards[0]
    # answers but no recall entry -> skipped
    answers = {card.questions[0].id: card.questions[0].correct_index}
    assert per_card_gaps({}, answers) == []
    # recall but no answers -> skipped
    assert per_card_gaps({card.card_id: 0.9}, {}) == []
