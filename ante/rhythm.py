# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Peak Hours: when do you actually perform best?

The "time back" thesis says the schedule is the lever \u2014 15 min at night, 30 in
the morning, 30 in the day. This module closes the loop by learning, from the
student's own review history, WHICH of those windows produces their best
accuracy, so the plan can put the hardest section in the sharpest window instead
of guessing.

Input is a list of (hour_of_day, correct) pairs derived from the review log
(``correct`` = the review was not an "Again"). Pure logic; unit-tested without
Anki. Abstains honestly until there is enough data.
"""

from __future__ import annotations

from .memory import wilson_interval

# named windows over the 24h clock (start inclusive, end exclusive; wraps at 24)
WINDOWS: list[tuple[str, int, int]] = [
    ("early morning", 5, 8),
    ("morning", 8, 12),
    ("afternoon", 12, 17),
    ("evening", 17, 21),
    ("night", 21, 29),  # 21:00\u201305:00 (wraps; 24+5)
]

MIN_TOTAL = 30
MIN_PER_WINDOW = 8


def _window_of(hour: int) -> str:
    h = hour % 24
    for name, start, end in WINDOWS:
        lo, hi = start % 24, end
        if hi <= 24:
            if lo <= h < hi:
                return name
        elif h >= lo or h < hi - 24:  # wraps midnight
            return name
    return "night"


def peak_windows(
    hour_outcomes: list[tuple[int, int]],
    min_total: int = MIN_TOTAL,
    min_per_window: int = MIN_PER_WINDOW,
) -> dict:
    """Accuracy by time-of-day window, the best window, and its edge over the
    student's own average. Abstains until there is enough evidence."""
    total = len(hour_outcomes)
    if total < min_total:
        return {
            "available": False,
            "reason": (
                f"only {total} timed reviews (need {min_total}) \u2014 keep studying to "
                "learn your peak hours"
            ),
            "n": total,
        }

    buckets: dict[str, list[int]] = {name: [] for name, _, _ in WINDOWS}
    for hour, correct in hour_outcomes:
        buckets[_window_of(int(hour))].append(1 if correct else 0)

    overall = sum(c for _, c in hour_outcomes) / total
    windows: list[dict] = []
    for name, _, _ in WINDOWS:
        b = buckets[name]
        if not b:
            continue
        n = len(b)
        acc = sum(b) / n
        lo, hi = wilson_interval(sum(b), n)
        windows.append(
            {
                "window": name,
                "n": n,
                "accuracy": round(acc, 4),
                "range": [round(lo, 4), round(hi, 4)],
                "delta": round(acc - overall, 4),
                "reliable": n >= min_per_window,
            }
        )

    reliable = [w for w in windows if w["reliable"]]
    ranked = sorted(reliable or windows, key=lambda w: w["accuracy"], reverse=True)
    best = ranked[0] if ranked else None
    worst = ranked[-1] if len(ranked) > 1 else None

    advice = None
    if best and worst and best["window"] != worst["window"]:
        edge = round((best["accuracy"] - worst["accuracy"]) * 100)
        if edge >= 5:
            advice = (
                f"You're about {edge}% sharper in the {best['window']} than the "
                f"{worst['window']}. Put your hardest section there."
            )
    if advice is None and best:
        advice = f"Your steadiest window is the {best['window']}."

    return {
        "available": True,
        "n": total,
        "overall_accuracy": round(overall, 4),
        "windows": sorted(windows, key=lambda w: w["accuracy"], reverse=True),
        "best_window": best["window"] if best else None,
        "best_accuracy": best["accuracy"] if best else None,
        "advice": advice,
    }
