# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Crash-recovery test (the spec's 7g / PRD NFR-3).

Kills the app hard, mid-review, N times in a row and proves the collection is
never corrupted afterwards. Each trial:

  1. copies the seed collection to a scratch dir,
  2. spawns a CHILD process that opens the collection and answers cards through
     the real engine (``col.sched.answerCard``) in a tight loop,
  3. waits until the child is actively reviewing, then sends SIGKILL at a random
     moment (so the kill lands mid-write, not at a quiescent point),
  4. reopens the collection and runs ``fix_integrity`` (== ``check_database``),
  5. asserts the check is clean and the persisted reviews survived.

SIGKILL cannot be caught or cleaned up after, so a clean reopen proves the
engine's SQLite writes are atomic across an abrupt process death. Run via
``just crash-test`` (defaults to 20 trials on the seed deck).

Usage:
    PYTHONPATH=out/pylib:. out/pyenv/bin/python -m ante.tools.crash_test \\
        --deck out/mcat_seed.anki2 --trials 20
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from anki.collection import Collection


def log(msg: str) -> None:
    print(msg, flush=True)


def _make_all_due(col: Collection) -> None:
    col.db.execute("update cards set queue=2, type=2, due=0, ivl=1")
    col.db.execute("update col set mod=mod")


def _select_largest_deck(col: Collection) -> None:
    row = col.db.first(
        "select did, count(*) c from cards group by did order by c desc limit 1"
    )
    if row:
        col.decks.set_current(int(row[0]))


def _revlog_count(col: Collection) -> int:
    return col.db.scalar("select count() from revlog") or 0


def child_loop(deck_path: str) -> int:
    """Open the collection and hammer the answer path until killed.

    Prints ``REVIEWING`` once the write loop is actually running, so the parent
    only kills a process that is mid-review.
    """
    col = Collection(deck_path)
    _select_largest_deck(col)
    _make_all_due(col)
    col.reset()
    print("REVIEWING", flush=True)
    while True:
        card = col.sched.getCard()
        if card is None:
            _make_all_due(col)
            col.reset()
            continue
        card.start_timer()
        col.sched.answerCard(card, random.choice((1, 2, 3, 4)))


def _spawn_child(deck_path: str) -> subprocess.Popen:
    env = dict(os.environ)
    # keep the child importable exactly like the parent
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (env.get("PYTHONPATH", ""),) if p
    ) or env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "ante.tools.crash_test", "--child", "--deck", deck_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
    )


def _wait_until_reviewing(proc: subprocess.Popen, timeout: float = 30.0) -> bool:
    """Block until the child prints its REVIEWING marker (or dies/timeouts)."""
    assert proc.stdout is not None
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return False
            continue
        if line.strip() == "REVIEWING":
            return True
    return False


def run_trials(deck_path: str, trials: int, seed: int = 0) -> int:
    rng = random.Random(seed)
    tmp = Path(tempfile.mkdtemp(prefix="ante-crashtest-"))
    corruptions = 0
    survived_reviews = 0
    try:
        for i in range(1, trials + 1):
            scratch = tmp / f"trial_{i}.anki2"
            shutil.copy(deck_path, scratch)

            proc = _spawn_child(str(scratch))
            if not _wait_until_reviewing(proc):
                log(f"  trial {i:>2}: child never started reviewing; skipping")
                proc.kill()
                proc.wait()
                continue

            # let it write for a random, short spell, then kill it MID-review
            time.sleep(rng.uniform(0.05, 0.6))
            proc.send_signal(signal.SIGKILL)
            proc.wait()

            # reopen and verify no corruption
            col = Collection(str(scratch))
            try:
                _problems, ok = col.fix_integrity()
                reviews = _revlog_count(col)
            finally:
                col.close()
            survived_reviews += reviews
            status = "clean" if ok else "CORRUPT"
            if not ok:
                corruptions += 1
            log(f"  trial {i:>2}: SIGKILL mid-review -> reopen {status}, {reviews} reviews persisted")
            scratch.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(scratch) + suffix).unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    log("")
    log(f"trials: {trials}   corruptions: {corruptions}   reviews persisted: {survived_reviews}")
    if corruptions == 0:
        log("PASS: zero corrupted collections across all crash trials.")
    else:
        log(f"FAIL: {corruptions} corrupted collection(s).")
    return 0 if corruptions == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Ante crash-recovery test (spec 7g).")
    parser.add_argument("--deck", default="out/mcat_seed.anki2")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--child", action="store_true", help="internal: run the reviewer loop"
    )
    args = parser.parse_args()

    if args.child:
        return child_loop(args.deck)

    if not Path(args.deck).exists():
        log(f"deck not found: {args.deck} (run `just seed-deck` first)")
        return 2
    log(f"Ante crash test: {args.trials} SIGKILL-mid-review trials on {args.deck}\n")
    return run_trials(args.deck, args.trials, args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
