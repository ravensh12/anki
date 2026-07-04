# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Cue-anchored implementation intentions (PRD 9.4) — Principle 2.

Gollwitzer's implementation-intention research: if-then plans anchored to a
stable existing routine ("before morning coffee, recall for 10 minutes") raise
follow-through far more than relying on willpower. Zimmerman frames consistency as
a trainable self-regulation skill that external structure can scaffold. This is
the opposite of nagging: notifications fire on the user's own chosen cues.

Pure logic + data; the Qt side renders the reminder. No tracking, no streaks.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CONFIG, AnteConfig

# approximate hour-of-day each named cue typically occurs, for scheduling order
_CUE_HOURS = {
    "before morning coffee": 7,
    "morning": 8,
    "lunch break": 12,
    "during the day": 15,
    "after dinner": 19,
    "night": 21,
}


@dataclass(frozen=True)
class ImplementationIntention:
    cue_text: str
    session_minutes: int
    enabled: bool = True

    @property
    def if_then(self) -> str:
        return f"If it's {self.cue_text}, then I recall for {self.session_minutes} min."

    def cue_hour(self) -> int:
        return _CUE_HOURS.get(self.cue_text.lower(), 12)

    def as_dict(self) -> dict:
        return {
            "cue_text": self.cue_text,
            "session_minutes": self.session_minutes,
            "enabled": self.enabled,
            "if_then": self.if_then,
        }


def default_intentions(
    cfg: AnteConfig | None = None,
) -> list[ImplementationIntention]:
    """Onboarding defaults (PRD 9.4): two cue-anchored 10-minute plans."""
    cfg = cfg or CONFIG
    m = cfg.default_session_minutes
    return [
        ImplementationIntention("before morning coffee", m),
        ImplementationIntention("night", m),
    ]


def next_due_intention(
    intentions: list[ImplementationIntention], current_hour: int
) -> ImplementationIntention | None:
    """The next enabled intention whose cue hour is at or after now; wraps to the
    earliest cue tomorrow if all today's have passed."""
    enabled = [i for i in intentions if i.enabled]
    if not enabled:
        return None
    upcoming = [i for i in enabled if i.cue_hour() >= current_hour]
    pool = upcoming or enabled
    return min(pool, key=lambda i: i.cue_hour())


def notification_text(intention: ImplementationIntention) -> str:
    """Cue-anchored, non-guilt reminder copy (Principle 4: never 'you broke your
    chain')."""
    return (
        f"{intention.cue_text.capitalize()}: a {intention.session_minutes}-minute "
        f"recall session is ready. Top of the stack is already chosen."
    )
