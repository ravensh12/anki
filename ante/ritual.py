# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The daily bookends — First Light and Last Light (Principles 1 + 2).

Ante's signature ritual: the day is bracketed by two short, high-leverage
sessions the science singles out —

  * **First Light** — morning retrieval, before the day starts (testing effect:
    cold recall beats warm rereading; starting before coffee/scrolling removes
    the decision entirely).
  * **Last Light** — a light pre-sleep review the brain consolidates overnight
    (sleep-dependent memory consolidation; the cheapest minutes of the day).

This module is the pure state machine behind that ritual: given today's review
activity by hour and the reminder schedule, it reports which bookend is done,
which is next, and honest no-shame copy. The bookends are also what the streak
counts — a day is "kept" when real study happened, not when the app was opened
(the effort gate stays in rewards.day_counts).
"""

from __future__ import annotations

from .reminders import WINDOW_HOURS

# Clock windows that count as each bookend (end-exclusive). Last Light runs to
# midnight; small-hours reviews are deliberately NOT counted — protecting sleep
# is part of the design, so a 2am session earns no ritual credit.
MORNING_HOURS = (4, 12)
NIGHT_HOURS = (19, 24)

FIRST_LIGHT = "first_light"
LAST_LIGHT = "last_light"


def _in_window(hour: int, window: tuple[int, int]) -> bool:
    return window[0] <= hour < window[1]


def _window_reviews(hour_counts: dict[int, int], window: tuple[int, int]) -> int:
    return sum(n for h, n in (hour_counts or {}).items() if _in_window(int(h), window))


def _scheduled_at(
    schedule: list[dict] | None, kinds: tuple[str, ...], default: tuple[int, int]
) -> str:
    for r in schedule or []:
        if r.get("kind") in kinds and r.get("at"):
            return str(r["at"])
    return f"{default[0]:02d}:{default[1]:02d}"


def bookends(
    hour_counts_today: dict[int, int],
    schedule: list[dict] | None = None,
    now_hour: int = 12,
    min_reviews: int = 1,
) -> dict:
    """Today's ritual state.

    ``hour_counts_today`` maps hour-of-day (0..23) to genuine reviews done in
    that hour today. ``schedule`` is the reminder schedule (dicts from
    reminders.build_schedule) used only to show each bookend's planned time.
    """
    morning_n = _window_reviews(hour_counts_today, MORNING_HOURS)
    night_n = _window_reviews(hour_counts_today, NIGHT_HOURS)
    morning_done = morning_n >= min_reviews
    night_done = night_n >= min_reviews

    morning_at = _scheduled_at(schedule, ("retrieval",), WINDOW_HOURS["morning"])
    night_at = _scheduled_at(schedule, ("encode",), WINDOW_HOURS["night"])

    if not morning_done and _in_window(now_hour, (0, MORNING_HOURS[1])):
        next_up = FIRST_LIGHT
    elif not night_done:
        next_up = LAST_LIGHT
    elif not morning_done:
        # evening passed, morning never happened — tomorrow's First Light is next
        next_up = FIRST_LIGHT
    else:
        next_up = None

    if morning_done and night_done:
        headline = "Both games kept — the day is banked."
    elif morning_done:
        headline = (
            "Morning game banked. A light hand at the midnight game and your "
            "brain files it overnight."
        )
    elif night_done:
        headline = "Midnight game banked. Tomorrow opens cold — that's the point."
    elif now_hour < MORNING_HOURS[1]:
        headline = "Sit down cold: recall before coffee beats rereading after it."
    elif now_hour < NIGHT_HOURS[0]:
        headline = "Morning slipped — no shame. Tonight's midnight game still counts."
    else:
        headline = "A short midnight hand now is the highest-leverage study of the day."

    return {
        "morning": {
            "key": FIRST_LIGHT,
            "label": "Morning Game",
            "done": morning_done,
            "reviews": morning_n,
            "at": morning_at,
            "detail": "cold recall — before coffee",
        },
        "night": {
            "key": LAST_LIGHT,
            "label": "Midnight Game",
            "done": night_done,
            "reviews": night_n,
            "at": night_at,
            "detail": "one light hand — sleep banks it",
        },
        "complete": morning_done and night_done,
        "next": next_up,
        "headline": headline,
    }


def night_shift(settled: int, loose: int, now_hour: int = 8) -> dict:
    """The consolidation-night report — the one mechanic that plays itself.

    Sleep-dependent consolidation is real scheduler math narrated as an
    overnight event: ``settled`` = distinct cards recalled successfully
    yesterday (their intervals grew — the night banked them), ``loose`` =
    cards whose review fell due overnight (the House pried them loose).
    Honest counts only; shown in the morning window, and only when something
    actually happened.
    """
    available = now_hour < MORNING_HOURS[1] and (settled > 0 or loose > 0)
    if settled > 0:
        headline = f"While you slept, the night shift banked {settled} cards."
    else:
        headline = "The House played all night."
    detail = ""
    if loose > 0:
        detail = (
            f"{loose} came loose in the dark — they're on the felt, "
            "waiting for the morning game."
        )
    elif settled > 0:
        detail = "Nothing came loose. Walk in and keep it that way."
    return {
        "available": available,
        "settled": int(settled),
        "loose": int(loose),
        "headline": headline,
        "detail": detail,
    }
