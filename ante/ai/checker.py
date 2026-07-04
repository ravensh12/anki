# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""AI card-quality checker.

Every generated card is classified before a student ever sees it:
  * CORRECT_USEFUL - supported and worth studying
  * WRONG          - contradicts a known answer or is unsupported by its source
  * BAD_TEACHING   - vague, trivial, or a duplicate

A wrong fact is worse than no card, so the pre-declared cutoff blocks anything
that is not CORRECT_USEFUL. We report the three counts the spec asks for.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .baseline import tokenize
from .provider import GeneratedCard


class Verdict(str, Enum):
    CORRECT_USEFUL = "correct_useful"
    WRONG = "wrong"
    BAD_TEACHING = "bad_teaching"


@dataclass(frozen=True)
class GoldItem:
    question: str
    answer: str


@dataclass(frozen=True)
class CardCheck:
    card: GeneratedCard
    verdict: Verdict
    reason: str
    passed: bool


@dataclass(frozen=True)
class CheckReport:
    correct_useful: int
    wrong: int
    bad_teaching: int
    total: int
    pass_rate: float
    batch_passes_cutoff: bool
    checks: list[CardCheck]

    def as_dict(self) -> dict:
        return {
            "correct_useful": self.correct_useful,
            "wrong": self.wrong,
            "bad_teaching": self.bad_teaching,
            "total": self.total,
            "pass_rate": round(self.pass_rate, 4),
            "batch_passes_cutoff": self.batch_passes_cutoff,
        }

    def passed_cards(self) -> list[GeneratedCard]:
        return [c.card for c in self.checks if c.passed]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def judge_card(
    card: GeneratedCard,
    gold: list[GoldItem],
    seen_fronts: set[str],
    *,
    answer_match: float = 0.3,
    question_match: float = 0.5,
) -> CardCheck:
    front_tokens = set(tokenize(card.front))
    back_tokens = set(tokenize(card.back))

    # --- bad teaching: trivial / vague / duplicate ---
    if len(card.front.split()) < 3 or not back_tokens:
        return CardCheck(card, Verdict.BAD_TEACHING, "too short/vague", False)
    norm_front = " ".join(sorted(front_tokens))
    if norm_front in seen_fronts:
        return CardCheck(card, Verdict.BAD_TEACHING, "duplicate question", False)
    if back_tokens and back_tokens <= front_tokens:
        return CardCheck(
            card, Verdict.BAD_TEACHING, "answer is given away in the question", False
        )

    # --- correctness against the gold set ---
    best_gold, best_q = None, 0.0
    for g in gold:
        q = _jaccard(front_tokens, set(tokenize(g.question)))
        if q > best_q:
            best_gold, best_q = g, q
    if best_gold is not None and best_q >= question_match:
        a = _jaccard(back_tokens, set(tokenize(best_gold.answer)))
        if a >= answer_match:
            return CardCheck(card, Verdict.CORRECT_USEFUL, "matches gold answer", True)
        return CardCheck(card, Verdict.WRONG, "contradicts a known gold answer", False)

    # --- no gold match: must be supported by its own source quote ---
    quote_tokens = set(tokenize(card.source_quote))
    support = _jaccard(back_tokens, quote_tokens)
    if quote_tokens and support >= answer_match:
        return CardCheck(
            card, Verdict.CORRECT_USEFUL, "supported by source quote", True
        )
    return CardCheck(card, Verdict.WRONG, "answer unsupported by source / gold", False)


def check_cards(
    cards: list[GeneratedCard],
    gold: list[GoldItem],
    batch_cutoff: float = 0.6,
) -> CheckReport:
    """Classify a batch. `batch_cutoff` is the minimum CORRECT_USEFUL fraction we
    declare acceptable *before* looking at results."""
    checks: list[CardCheck] = []
    seen: set[str] = set()
    for card in cards:
        chk = judge_card(card, gold, seen)
        checks.append(chk)
        seen.add(" ".join(sorted(set(tokenize(card.front)))))

    correct = sum(1 for c in checks if c.verdict is Verdict.CORRECT_USEFUL)
    wrong = sum(1 for c in checks if c.verdict is Verdict.WRONG)
    bad = sum(1 for c in checks if c.verdict is Verdict.BAD_TEACHING)
    total = len(checks)
    pass_rate = correct / total if total else 0.0
    return CheckReport(
        correct_useful=correct,
        wrong=wrong,
        bad_teaching=bad,
        total=total,
        pass_rate=pass_rate,
        batch_passes_cutoff=pass_rate >= batch_cutoff,
        checks=checks,
    )
