# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The paraphrase test (spec 7d), done literally.

Take 30 real seed cards. For each, we authored TWO exam-style questions that
test the *same idea* in new words (``ante/data/paraphrase_set.json``). This
module compares, per card, the student's **recall** on the source card against
their **accuracy on the two reworded questions**. If the two numbers track each
other almost exactly, the "performance" signal is just memory in disguise and we
have NOT built the memory->performance bridge. We report the per-card gap and the
aggregate.

Pure logic + data, so it is unit-testable without Anki. A ``__main__`` entry
point runs the test against a student response log, or — with ``--demo`` —
against two synthetic students (a memorizer vs. a transfer learner) so the
mechanism's output is visible for the results report.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).with_name("data") / "paraphrase_set.json"


@dataclass(frozen=True)
class PPQuestion:
    id: str
    stem: str
    choices: tuple[str, ...]
    correct_index: int


@dataclass(frozen=True)
class PPCard:
    card_id: str
    topic: str
    card_front: str
    card_back: str
    idea: str
    questions: tuple[PPQuestion, ...]


@lru_cache(maxsize=2)
def load_cards(path: str | None = None) -> tuple[PPCard, ...]:
    raw = json.loads(Path(path or DATA_PATH).read_text(encoding="utf-8"))
    out: list[PPCard] = []
    for c in raw["cards"]:
        questions = tuple(
            PPQuestion(
                id=q["id"],
                stem=q["stem"],
                choices=tuple(q["choices"]),
                correct_index=int(q["correct_index"]),
            )
            for q in c["questions"]
        )
        out.append(
            PPCard(
                card_id=c["card_id"],
                topic=c["topic"],
                card_front=c["card_front"],
                card_back=c["card_back"],
                idea=c["idea"],
                questions=questions,
            )
        )
    return tuple(out)


def all_questions(path: str | None = None) -> list[PPQuestion]:
    return [q for c in load_cards(path) for q in c.questions]


def question_by_id(qid: str, path: str | None = None) -> PPQuestion | None:
    return next((q for q in all_questions(path) if q.id == qid), None)


def question_correct(qid: str, chosen_index: int, path: str | None = None) -> bool:
    q = question_by_id(qid, path)
    return bool(q and int(chosen_index) == q.correct_index)


@dataclass(frozen=True)
class CardParaphrase:
    """One card's paraphrase result: recall on the card vs accuracy on its
    reworded questions."""

    card_id: str
    topic: str
    recall: float
    n_questions: int
    reworded_accuracy: float

    @property
    def gap(self) -> float:
        return self.recall - self.reworded_accuracy

    def as_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "topic": self.topic,
            "recall": round(self.recall, 4),
            "n_questions": self.n_questions,
            "reworded_accuracy": round(self.reworded_accuracy, 4),
            "gap": round(self.gap, 4),
        }


def per_card_gaps(
    recall_by_card: Mapping[str, float],
    answers: Mapping[str, int],
    path: str | None = None,
) -> list[CardParaphrase]:
    """For every card that has BOTH a recall number and at least one answered
    reworded question, compute (recall, reworded accuracy). ``answers`` maps a
    question id to the chosen index."""
    rows: list[CardParaphrase] = []
    for card in load_cards(path):
        if card.card_id not in recall_by_card:
            continue
        answered = [q for q in card.questions if q.id in answers]
        if not answered:
            continue
        correct = sum(
            1 for q in answered if int(answers[q.id]) == q.correct_index
        )
        rows.append(
            CardParaphrase(
                card_id=card.card_id,
                topic=card.topic,
                recall=float(recall_by_card[card.card_id]),
                n_questions=len(answered),
                reworded_accuracy=correct / len(answered),
            )
        )
    return rows


@dataclass(frozen=True)
class ParaphraseSummary:
    n_cards: int
    mean_recall: float
    mean_reworded_accuracy: float
    gap: float
    rows: tuple[CardParaphrase, ...]

    @property
    def meaningful(self) -> bool:
        # If recall and transfer accuracy move together (tiny gap), the
        # performance model is not measuring anything beyond memory.
        return self.gap > 0.05

    def as_dict(self) -> dict:
        return {
            "n_cards": self.n_cards,
            "mean_card_recall": round(self.mean_recall, 4),
            "mean_reworded_accuracy": round(self.mean_reworded_accuracy, 4),
            "gap": round(self.gap, 4),
            "meaningful": self.meaningful,
            "rows": [r.as_dict() for r in self.rows],
        }


def summarize(
    recall_by_card: Mapping[str, float],
    answers: Mapping[str, int],
    path: str | None = None,
) -> ParaphraseSummary:
    rows = per_card_gaps(recall_by_card, answers, path)
    n = len(rows)
    if n == 0:
        return ParaphraseSummary(0, 0.0, 0.0, 0.0, ())
    mr = sum(r.recall for r in rows) / n
    mw = sum(r.reworded_accuracy for r in rows) / n
    return ParaphraseSummary(n, mr, mw, mr - mw, tuple(rows))


# --- demonstration students (for the results report; clearly synthetic) ------


def _demo_students(path: str | None = None) -> dict[str, tuple[dict, dict]]:
    """Two synthetic students over the full 30-card set, to show the test
    discriminates memory from transfer:

      * "memorizer" recalls every card (0.95) but only guesses the reworded
        questions (~chance), so the gap is large and positive.
      * "transfer" recalls the cards (0.95) AND answers the reworded questions
        correctly, so the gap is near zero.

    These are illustrative, not real student data; the same code path scores a
    real response log passed via --answers/--recall.
    """
    cards = load_cards(path)
    recall = {c.card_id: 0.95 for c in cards}
    memorizer: dict[str, int] = {}
    transfer: dict[str, int] = {}
    for c in cards:
        for i, q in enumerate(c.questions):
            # memorizer: gets the first reworded question wrong, second right
            # (~50% transfer) despite full recall -> a real, visible gap
            memorizer[q.id] = q.correct_index if i % 2 == 1 else (q.correct_index + 1) % len(q.choices)
            transfer[q.id] = q.correct_index
    return {
        "memorizer": (recall, memorizer),
        "transfer": (recall, transfer),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paraphrase test (spec 7d).")
    parser.add_argument("--answers", type=Path, help="JSON {question_id: choice}")
    parser.add_argument("--recall", type=Path, help="JSON {card_id: recall 0..1}")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run two synthetic students (memorizer vs transfer) to show the gap",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cards = load_cards()
    n_q = sum(len(c.questions) for c in cards)

    if args.demo or not (args.answers and args.recall):
        if not args.demo:
            print(
                f"paraphrase set: {len(cards)} cards x 2 reworded questions "
                f"= {n_q} items.\nNo --answers/--recall given; showing the "
                "synthetic demonstration (--demo).\n"
            )
        results = {}
        for name, (recall, answers) in _demo_students().items():
            results[name] = summarize(recall, answers).as_dict()
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for name, s in results.items():
                print(
                    f"{name:>10}: recall={s['mean_card_recall']:.2f} "
                    f"reworded={s['mean_reworded_accuracy']:.2f} "
                    f"gap={s['gap']:+.2f} "
                    f"({'REAL bridge' if s['meaningful'] else 'no gap - memory in disguise'})"
                )
        return 0

    answers = json.loads(args.answers.read_text(encoding="utf-8"))
    recall = json.loads(args.recall.read_text(encoding="utf-8"))
    summary = summarize(recall, answers).as_dict()
    print(json.dumps(summary, indent=2) if args.json else summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
