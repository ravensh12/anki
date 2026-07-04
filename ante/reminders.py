# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""When-to-study reminders (Principle 2: the system decides the next action).

Turns the recalibrated day-plan into a concrete notification schedule with copy
grounded in learning science, not nagging:

  * Morning -> retrieval practice (testing beats rereading; Roediger & Karpicke).
  * Midday  -> spaced review (catch cards before the forgetting curve resets).
  * Night   -> a light pre-sleep review that the brain consolidates overnight
               (this is the "~30 cards before bed" cue).
  * Countdown -> as the exam nears, a calm urgency, never shame.

Every reminder is cue-anchored (Gollwitzer implementation intentions), states the
exact next action ("N cards, ~M min, top of the stack already chosen"), and is
suppressed inside quiet hours to protect sleep. Pure logic; the Qt layer schedules
delivery with a timer + the system tray.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CONFIG, AnteConfig
from .profile import StudyProfile

# default clock time each named window fires at
WINDOW_HOURS: dict[str, tuple[int, int]] = {
    "morning": (8, 0),
    "during the day": (14, 0),
    "night": (21, 0),
}

# role -> notification kind
_ROLE_KIND = {"new": "retrieval", "review": "review", "encode": "encode"}


@dataclass(frozen=True)
class Reminder:
    hour: int
    minute: int
    window: str
    kind: str
    title: str
    body: str

    @property
    def minutes_of_day(self) -> int:
        return self.hour * 60 + self.minute

    def as_dict(self) -> dict:
        return {
            "hour": self.hour,
            "minute": self.minute,
            "window": self.window,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "at": f"{self.hour:02d}:{self.minute:02d}",
        }


def _card_target(minutes: int, sec_per_card: float) -> int:
    return max(1, int(minutes * 60 / max(1.0, sec_per_card)))


def _copy(
    kind: str,
    cards: int,
    minutes: int,
    due_count: int,
    days_remaining: int | None,
    best_next_topic: str | None,
) -> tuple[str, str]:
    topic = (
        (best_next_topic or "")
        .replace("mcat::", "")
        .replace("::", " · ")
        .replace("_", " ")
    )
    n = min(cards, due_count) if due_count > 0 else cards
    ahead = due_count == 0
    if kind == "retrieval":
        title = "The morning game opens"
        body = (
            f"~{n} cards on the felt (~{minutes} min). Cold recall beats warm "
            "rereading — the deck is already stacked in your favor."
        )
        if ahead:
            body = f"Nothing due — a ~{minutes} min warm-up hand keeps you loose. No pressure."
    elif kind == "encode":
        title = "Last hand before lights out"
        body = (
            f"~{n} cards (~{minutes} min). Play them now and your brain banks "
            "them overnight — the cheapest minutes of the day."
        )
        if ahead:
            body = (
                f"Optional midnight hand (~{minutes} min) — light, then lights out."
            )
    else:  # review
        title = "Midday — protect your stack"
        body = (
            f"~{n} cards (~{minutes} min) are ripe. A few minutes now and the "
            "House doesn't claw them back."
        )
        if ahead:
            body = "You're clear for now. Rest is part of the schedule."
    if topic and not ahead:
        body += f" First card up: {topic}."
    if days_remaining is not None and 0 <= days_remaining <= 21 and not ahead:
        body += f" ({days_remaining}d to the final table)"
    return title, body


def build_schedule(
    profile: StudyProfile,
    slot_plan: list[dict],
    *,
    due_count: int = 0,
    best_next_topic: str | None = None,
    days_remaining: int | None = None,
    sec_per_card: float | None = None,
    cfg: AnteConfig | None = None,
) -> list[Reminder]:
    """The day's reminder schedule (empty if the student turned reminders off)."""
    cfg = cfg or CONFIG
    if not profile.reminders_enabled:
        return []
    spc = sec_per_card if sec_per_card is not None else cfg.seconds_per_card
    out: list[Reminder] = []
    for slot in slot_plan:
        window = slot.get("window", "")
        minutes = int(slot.get("minutes", 0))
        if minutes <= 0 or window not in WINDOW_HOURS:
            continue
        hour, minute = WINDOW_HOURS[window]
        if profile.in_quiet_hours(hour):
            continue
        kind = _ROLE_KIND.get(slot.get("role", "review"), "review")
        cards = _card_target(minutes, spc)
        title, body = _copy(
            kind, cards, minutes, due_count, days_remaining, best_next_topic
        )
        out.append(Reminder(hour, minute, window, kind, title, body))
    out.sort(key=lambda r: r.minutes_of_day)
    return out


def next_reminder(
    schedule: list[Reminder], now_hour: int, now_minute: int = 0
) -> Reminder | None:
    """The next reminder at or after now; wraps to the first one tomorrow."""
    if not schedule:
        return None
    now = now_hour * 60 + now_minute
    upcoming = [r for r in schedule if r.minutes_of_day >= now]
    return upcoming[0] if upcoming else schedule[0]


def what_to_do_now(
    *,
    due_count: int,
    best_next_topic: str | None,
    recommended_daily_minutes: int,
    now_hour: int,
    sec_per_card: float = 8.0,
) -> dict:
    """The single 'right now' instruction (Principle 2: remove the decision). Used by
    the in-app hero and as a manual-notification body."""
    topic = (
        (best_next_topic or "")
        .replace("mcat::", "")
        .replace("::", " · ")
        .replace("_", " ")
    )
    if due_count <= 0:
        return {
            "headline": "You're clear",
            "detail": "Nothing is due. Rest is part of the schedule, or play ahead.",
            "cards": 0,
            "minutes": 0,
        }
    # a right-sized bite for this moment: a 10-15 min slice of the due stack
    slice_min = 10 if now_hour >= 21 else 15
    cards = min(due_count, _card_target(slice_min, sec_per_card))
    detail = f"{cards} highest-stakes cards (~{slice_min} min)."
    if topic:
        detail += f" Start at {topic}."
    return {
        "headline": "Take your seat",
        "detail": detail,
        "cards": cards,
        "minutes": slice_min,
    }
