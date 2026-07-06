# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""One-command engine benchmark (the spec's `make bench`).

Loads a deck and reports p50 / p95 / worst-case latency for the core actions,
so a single hand-picked number can't hide a slow tail. Run via `just bench`.

    just bench out/mcat_seed.anki2 200

Targets (from the spec section 10, desktop):
  * button press acknowledged (answerCard): p95 < 50 ms
  * next card after grading:                p95 < 100 ms
  * dashboard first load:                   p95 < 1000 ms
  * dashboard refresh:                      p95 < 500 ms
  * app cold start (open collection):       p95 < 5000 ms
  * memory on the deck:                     under a stated limit (report peak RSS)

Sync timing (< 5 s for a normal session) is measured by `just sync-test`, and
phone-side latency by `just ios-swift-smoke`; both are out of this process's
scope. Build a 50k-card deck with `just seed-deck 1400` to bench at scale.
"""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from anki.collection import Collection

# stated desktop memory limit for the deck (spec 10: "under a limit you state").
# 1 GiB is generous headroom for a 50k-card collection held fully in the engine.
MEMORY_LIMIT_MB = 1024.0


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


@contextmanager
def _scratch_copy(deck_path: str):
    """Benchmark against a throwaway copy so a real deck isn't mutated (the
    answer-path measurement advances the scheduler and would persist)."""
    tmp = Path(tempfile.mkdtemp(prefix="ante-bench-"))
    scratch = tmp / "bench.anki2"
    shutil.copy(deck_path, scratch)
    try:
        yield str(scratch)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _card_count(deck_path: str) -> int:
    col = Collection(deck_path)
    try:
        return col.db.scalar("select count() from cards") or 0
    finally:
        col.close()


def _make_all_due(col: Collection) -> None:
    """Force every card into a due review state (raw SQL); the v3 scheduler picks
    this up lazily on the next fetch, so no explicit reset is required."""
    col.db.execute("update cards set queue=2, type=2, due=0, ivl=1")
    col.db.execute("update col set mod=mod")  # bump so caches re-read


def _raise_daily_limits(col: Collection) -> None:
    """Lift the per-deck new/review caps so the answer loop can pull enough
    cards to build a stable latency sample (the default 200/day would truncate)."""
    for conf in col.decks.all_config():
        conf["rev"]["perDay"] = 100000
        conf["new"]["perDay"] = 100000
        col.decks.update_config(conf)


def _peak_rss_mb() -> float:
    """Peak resident set size of this process, in MB (platform-normalized)."""
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes
    return ru / (1024.0 * 1024.0) if sys.platform == "darwin" else ru / 1024.0


def measure_cold_start(deck_path: str, iters: int) -> Stat:
    """Time opening the collection from cold (the engine's cold-start path)."""
    samples = []
    for _ in range(iters):
        t = time.perf_counter()
        col = Collection(deck_path)
        samples.append((time.perf_counter() - t) * 1000)
        col.close()
    return _summarize("cold_start_open", samples, 5000.0)


def benchmark(deck_path: str, iters: int) -> tuple[list[Stat], float]:
    # cold start first, on its own fresh opens (fewer iters — it's the slow path)
    stats: list[Stat] = [measure_cold_start(deck_path, min(iters, 20))]

    col = Collection(deck_path)
    try:
        _select_largest_deck(col)

        # dashboard FIRST LOAD (cold query) vs REFRESH (warmed) — the spec sets
        # separate targets for each, so we measure them separately.
        t = time.perf_counter()
        col._backend.get_topic_mastery(
            search="", topic_prefix="", mastery_threshold=0.0
        )
        first_load_ms = (time.perf_counter() - t) * 1000
        stats.append(Stat("dashboard_first_load", first_load_ms, first_load_ms, first_load_ms, 1, 1000.0))

        samples = []
        for _ in range(iters):
            t = time.perf_counter()
            col._backend.get_topic_mastery(
                search="", topic_prefix="", mastery_threshold=0.0
            )
            samples.append((time.perf_counter() - t) * 1000)
        stats.append(_summarize("dashboard_refresh", samples, 500.0))

        # fetch the next queued batch (queue build + first card)
        _make_all_due(col)
        samples = []
        for _ in range(iters):
            t = time.perf_counter()
            col._backend.get_queued_cards(fetch_limit=1, intraday_learning_only=False)
            samples.append((time.perf_counter() - t) * 1000)
        stats.append(_summarize("next_card_fetch", samples, 100.0))

        # button press acknowledged (answerCard alone, spec target 50 ms) and the
        # answer+next round-trip (spec target 100 ms). Persist the due-state
        # change and reopen so the v3 scheduler rebuilds its queue from fresh.
        _make_all_due(col)
        col.close()
        col = Collection(deck_path)
        _select_largest_deck(col)
        _raise_daily_limits(col)  # so the sample isn't capped at the 200/day cap
        ack_samples = []
        rt_samples = []
        for _ in range(iters):
            card = col.sched.getCard()
            if card is None:
                # queue drained (daily limit / learning steps) — top it back up
                _make_all_due(col)
                card = col.sched.getCard()
                if card is None:
                    break
            t = time.perf_counter()
            col.sched.answerCard(card, 3)
            ack_samples.append((time.perf_counter() - t) * 1000)
            col.sched.getCard()  # time-to-next-card
            rt_samples.append((time.perf_counter() - t) * 1000)
        if ack_samples:
            stats.append(_summarize("button_press_ack", ack_samples, 50.0))
            stats.append(_summarize("answer_then_next_card", rt_samples, 100.0))

        return stats, _peak_rss_mb()
    finally:
        col.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Ante engine actions.")
    parser.add_argument("--deck", default="out/mcat_seed.anki2")
    parser.add_argument("--iters", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not Path(args.deck).exists():
        print(f"deck not found: {args.deck} (run `just seed-deck` first)")
        return 2

    with _scratch_copy(args.deck) as scratch:
        card_count = _card_count(scratch)
        stats, peak_rss_mb = benchmark(scratch, args.iters)
    mem_ok = peak_rss_mb <= MEMORY_LIMIT_MB

    if args.json:
        print(
            json.dumps(
                {
                    "deck": args.deck,
                    "cards": card_count,
                    "iters": args.iters,
                    "actions": [s.as_dict() for s in stats],
                    "peak_rss_mb": round(peak_rss_mb, 1),
                    "memory_limit_mb": MEMORY_LIMIT_MB,
                    "memory_ok": mem_ok,
                },
                indent=2,
            )
        )
    else:
        print(f"\nAnte benchmark on {args.deck} — {card_count} cards ({args.iters} iters)\n")
        print(f"{'action':<28} {'p50':>8} {'p95':>8} {'worst':>8}  status")
        print("-" * 64)
        for s in stats:
            status = "ok" if s.ok else "SLOW"
            print(
                f"{s.action:<28} {s.p50_ms:>7.2f}m {s.p95_ms:>7.2f}m "
                f"{s.worst_ms:>7.2f}m  {status}"
            )
        print("-" * 64)
        print(
            f"{'peak memory (RSS)':<28} {peak_rss_mb:>7.1f}MB           "
            f"limit {MEMORY_LIMIT_MB:.0f}MB  {'ok' if mem_ok else 'OVER'}"
        )
    return 0 if all(s.ok for s in stats) and mem_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
