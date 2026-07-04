# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""AI evaluation harness.

Runs BEFORE any generated content reaches a student and produces two things the
spec asks for:

1. An answer-selection benchmark on a held-out gold set, comparing the AI
   provider against keyword and TF-IDF baselines (accuracy + wrong-answer rate).
2. A card-quality gate: generate cards from a real source, run them through the
   checker, and report correct / wrong / bad-teaching counts, emitting only the
   cards that pass the pre-declared cutoff.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from .baseline import TfidfRetriever, keyword_best
from .checker import CheckReport, GoldItem, check_cards
from .generate import generate_cards
from .provider import Provider, get_provider


@dataclass(frozen=True)
class MethodScore:
    method: str
    accuracy: float
    wrong_rate: float
    n: int

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "accuracy": round(self.accuracy, 4),
            "wrong_rate": round(self.wrong_rate, 4),
            "n": self.n,
        }


def answer_selection_eval(
    gold: list[GoldItem], provider: Provider, cutoff: float = 0.5
) -> dict:
    """Each method must pick the correct answer for each gold question out of all
    gold answers. Reports accuracy + wrong-rate per method."""
    candidates = [g.answer for g in gold]
    tfidf = TfidfRetriever(candidates)
    context = "\n".join(candidates)
    n = len(gold)

    ai_correct = kw_correct = tf_correct = 0
    for i, g in enumerate(gold):
        if keyword_best(g.question, candidates) == i:
            kw_correct += 1
        if tfidf.best(g.question) == i:
            tf_correct += 1
        ans = provider.answer(g.question, context)
        ai_sel = tfidf.best(ans) if ans else -1
        if ai_sel == i:
            ai_correct += 1

    ai = MethodScore("ai_" + provider.name, ai_correct / n, 1 - ai_correct / n, n)
    kw = MethodScore("keyword", kw_correct / n, 1 - kw_correct / n, n)
    tf = MethodScore("tfidf", tf_correct / n, 1 - tf_correct / n, n)
    best_baseline = max(kw.accuracy, tf.accuracy)
    return {
        "ai": ai.as_dict(),
        "baselines": [kw.as_dict(), tf.as_dict()],
        "ai_beats_baselines": ai.accuracy >= best_baseline,
        "meets_cutoff": ai.accuracy >= cutoff,
        "cutoff": cutoff,
    }


def card_quality_eval(
    source: str,
    source_id: str,
    gold: list[GoldItem],
    provider: Provider,
    max_cards: int = 50,
    batch_cutoff: float = 0.6,
) -> tuple[dict, CheckReport]:
    gen = generate_cards(source, source_id, max_cards=max_cards, provider=provider)
    report = check_cards(gen.cards, gold, batch_cutoff=batch_cutoff)
    return (
        {
            "generation": {
                "generator": gen.generator,
                "rejected_reason": gen.rejected_reason,
                "n_generated": len(gen.cards),
            },
            "quality": report.as_dict(),
            "emitted_cards": [c.as_dict() for c in report.passed_cards()],
        },
        report,
    )


def load_gold(path: Path) -> list[GoldItem]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw["items"] if isinstance(raw, dict) else raw
    return [GoldItem(question=i["question"], answer=i["answer"]) for i in items]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Ante AI eval harness.")
    parser.add_argument(
        "--gold",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "gold_set.json",
    )
    parser.add_argument("--source", type=Path, help="source text to generate from")
    parser.add_argument("--offline", action="store_true", help="force offline provider")
    parser.add_argument("--out", type=Path, default=Path("out/ai_eval.json"))
    args = parser.parse_args()

    provider = get_provider(force_offline=args.offline)
    gold = load_gold(args.gold)

    result: dict = {"provider": provider.name}
    result["answer_selection"] = answer_selection_eval(gold, provider)

    if args.source and args.source.exists():
        source = args.source.read_text(encoding="utf-8")
        quality, _ = card_quality_eval(source, args.source.name, gold, provider)
        result["card_quality"] = quality

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    asel = result["answer_selection"]
    print(f"provider: {provider.name}")
    print(
        f"answer-selection  AI acc={asel['ai']['accuracy']:.2%}  "
        f"keyword={asel['baselines'][0]['accuracy']:.2%}  "
        f"tfidf={asel['baselines'][1]['accuracy']:.2%}  "
        f"(beats baselines: {asel['ai_beats_baselines']})"
    )
    if "card_quality" in result:
        q = result["card_quality"]["quality"]
        print(
            f"card quality      correct={q['correct_useful']} wrong={q['wrong']} "
            f"bad={q['bad_teaching']} (pass cutoff: {q['batch_passes_cutoff']})"
        )
    print(f"report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
