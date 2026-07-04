# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Response-time analytics — because HOW you answer is data too.

Every card and every quiz item carries how long it took. Correct-and-fast is
fluent recall; correct-but-slow is effortful (still fragile); wrong-and-fast is a
careless slip (not a knowledge gap — slow down); wrong-and-slow is a genuine
struggle (needs re-teaching, not just reps). Separating these turns raw accuracy
into an actionable read and lets the coach say "your misses are careless, not
ignorance" — a very different fix.

Pure logic; unit-testable without Anki. The Qt layer supplies (correct, ms) pairs
from the review log and the quiz attempt log.
"""

from __future__ import annotations

from collections.abc import Iterable

from .config import CONFIG, AnteConfig

FLUENT = "fluent"
EFFORTFUL = "effortful"
CARELESS = "careless"
STRUGGLED = "struggled"
UNKNOWN = "unknown"


def classify_response(
    correct: bool, elapsed_ms: int | None, cfg: AnteConfig | None = None
) -> str:
    """One of fluent / effortful / careless / struggled / unknown."""
    cfg = cfg or CONFIG
    if elapsed_ms is None or elapsed_ms <= 0:
        return UNKNOWN
    if correct:
        return FLUENT if elapsed_ms <= cfg.fluent_ms else EFFORTFUL
    return CARELESS if elapsed_ms <= cfg.careless_ms else STRUGGLED


def timing_summary(
    events: Iterable[tuple[bool, int | None]], cfg: AnteConfig | None = None
) -> dict:
    """Aggregate (correct, elapsed_ms) events into counts, fractions, a median-ish
    pace, and a single plain-language insight."""
    cfg = cfg or CONFIG
    counts = {FLUENT: 0, EFFORTFUL: 0, CARELESS: 0, STRUGGLED: 0}
    timed = 0
    total = 0
    ms_values: list[int] = []
    for correct, ms in events:
        total += 1
        kind = classify_response(bool(correct), ms, cfg)
        if kind == UNKNOWN:
            continue
        timed += 1
        counts[kind] += 1
        if ms:
            ms_values.append(int(ms))

    if timed == 0:
        return {
            "available": False,
            "reason": "no timed answers yet",
            "n": total,
        }

    wrong = counts[CARELESS] + counts[STRUGGLED]
    careless_of_wrong = counts[CARELESS] / wrong if wrong else 0.0
    fluent_frac = counts[FLUENT] / timed
    ms_values.sort()
    median_ms = ms_values[len(ms_values) // 2] if ms_values else 0

    if wrong and careless_of_wrong >= 0.4:
        insight = (
            f"{round(careless_of_wrong * 100)}% of your misses were fast slips, not "
            "gaps — slow down and read the full stem before answering."
        )
    elif counts[EFFORTFUL] and counts[EFFORTFUL] >= counts[FLUENT]:
        insight = (
            "You're getting them right but slowly — recall is still effortful. More "
            "spaced reps will turn effort into fluency (and buy time on test day)."
        )
    elif fluent_frac >= 0.6:
        insight = (
            f"{round(fluent_frac * 100)}% of your correct answers were fast and "
            "automatic — that's real, test-ready fluency."
        )
    else:
        insight = "A healthy mix — keep converting effortful hits into fast ones."

    return {
        "available": True,
        "n": timed,
        "counts": counts,
        "fluent_fraction": round(fluent_frac, 4),
        "careless_of_wrong": round(careless_of_wrong, 4),
        "median_ms": median_ms,
        "insight": insight,
    }
