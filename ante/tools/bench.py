# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""One-command engine benchmark (the spec's `make bench`).

Loads a deck and reports p50 / p95 / worst-case latency for the core actions,
so a single hand-picked number can't hide a slow tail. Run via `just bench`.

    just bench out/mcat_seed.anki2 200

Targets (from the spec, desktop):
  * next card after grading: p95 < 100 ms
  * dashboard first load: p95 < 1000 ms
  * dashboard refresh:     p95 < 500 ms
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass

from anki.collection import Collection


@dataclass
class Stat:
    action: str
    p50_ms: float
    p95_ms: float
    worst_ms: float
    n: int
    target_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.target_ms is None or self.p95_ms <= self.target_ms

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "worst_ms": round(self.worst_ms, 2),
            "n": self.n,
            "target_p95_ms": self.target_ms,
            "ok": self.ok,
        }


def _percentile(sorted_ms: list[float], q: float) -> float:
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, int(q * len(sorted_ms)))
    return sorted_ms[idx]


def _summarize(action: str, samples_ms: list[float], target_ms: float | None) -> Stat:
    s = sorted(samples_ms)
    return Stat(
        action=action,
        p50_ms=_percentile(s, 0.50),
        p95_ms=_percentile(s, 0.95),
        worst_ms=s[-1] if s else 0.0,
        n=len(s),
        target_ms=target_ms,
    )


def _select_largest_deck(col: Collection) -> None:
    """Study the deck that actually holds cards (the scheduler studies the
    currently selected deck)."""
    row = col.db.first(
        "select did, count(*) c from cards group by did order by c desc limit 1"
    )
    if row:
        col.decks.set_current(int(row[0]))


def _make_all_due(col: Collection) -> None:
    """Force every card into a due review state (raw SQL); the v3 scheduler picks
    this up lazily on the next fetch, so no explicit reset is required."""
    col.db.execute("update cards set queue=2, type=2, due=0, ivl=1")
    col.db.execute("update col set mod=mod")  # bump so caches re-read


def benchmark(deck_path: str, iters: int) -> list[Stat]:
    col = Collection(deck_path)
    try:
        _select_largest_deck(col)
        stats: list[Stat] = []

        # dashboard / mastery query (our engine change)
        samples = []
        for _ in range(iters):
            t = time.perf_counter()
            col._backend.get_topic_mastery(
                search="", topic_prefix="", mastery_threshold=0.0
            )
            samples.append((time.perf_counter() - t) * 1000)
        stats.append(_summarize("dashboard_mastery_query", samples, 1000.0))

        # fetch the next queued batch (queue build + first card)
        _make_all_due(col)
        samples = []
        for _ in range(iters):
            t = time.perf_counter()
            col._backend.get_queued_cards(fetch_limit=1, intraday_learning_only=False)
            samples.append((time.perf_counter() - t) * 1000)
        stats.append(_summarize("next_card_fetch", samples, 100.0))

        # answer + next card round-trip. Persist the due-state change and reopen
        # so the v3 scheduler rebuilds its queue from fresh state.
        _make_all_due(col)
        col.close()
        col = Collection(deck_path)
        _select_largest_deck(col)
        samples = []
        for _ in range(iters):
            card = col.sched.getCard()
            if card is None:
                break
            t = time.perf_counter()
            col.sched.answerCard(card, 3)
            col.sched.getCard()  # time-to-next-card included
            samples.append((time.perf_counter() - t) * 1000)
        if samples:
            stats.append(_summarize("answer_then_next_card", samples, 100.0))

        return stats
    finally:
        col.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Ante engine actions.")
    parser.add_argument("--deck", default="out/mcat_seed.anki2")
    parser.add_argument("--iters", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    stats = benchmark(args.deck, args.iters)

    if args.json:
        print(json.dumps([s.as_dict() for s in stats], indent=2))
    else:
        print(f"\nAnte benchmark on {args.deck} ({args.iters} iters)\n")
        print(f"{'action':<28} {'p50':>8} {'p95':>8} {'worst':>8}  status")
        print("-" * 64)
        for s in stats:
            status = "ok" if s.ok else "SLOW"
            print(
                f"{s.action:<28} {s.p50_ms:>7.2f}m {s.p95_ms:>7.2f}m "
                f"{s.worst_ms:>7.2f}m  {status}"
            )
    return 0 if all(s.ok for s in stats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
