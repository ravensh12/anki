# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Micro-sessions (PRD 9.2) — the time-back promise in session form.

The product narrative is increments, not 10-hour crams (Principles 1 + 2). A session is
DEFAULT_SESSION_MINUTES of the value-ordered due stack, endable cleanly at any
time without losing data. Sessions lower the activation energy of the next
correct action (Steel 2007) and keep practice distributed (Cepeda 2006).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CONFIG, AnteConfig


@dataclass(frozen=True)
class MicroSession:
    minutes: int
    target_cards: int
    due_count: int
    sec_per_card: float
    clears_due: bool

    def as_dict(self) -> dict:
        return {
            "minutes": self.minutes,
            "target_cards": self.target_cards,
            "due_count": self.due_count,
            "sec_per_card": self.sec_per_card,
            "clears_due": self.clears_due,
        }


def plan_micro_session(
    due_count: int,
    minutes: int | None = None,
    cfg: AnteConfig | None = None,
) -> MicroSession:
    """A single bite-sized session: how many of the highest-value due cards fit in
    the time box. Capacity is the cap; we never invent work beyond what's due."""
    cfg = cfg or CONFIG
    minutes = minutes or cfg.default_session_minutes
    capacity = int(minutes * 60 / cfg.seconds_per_card)
    target = min(capacity, due_count)
    return MicroSession(
        minutes=minutes,
        target_cards=target,
        due_count=due_count,
        sec_per_card=cfg.seconds_per_card,
        clears_due=capacity >= due_count,
    )


def daily_plan(
    due_count: int,
    budget_minutes: int,
    slots: list[tuple[str, int]] | None = None,
    cfg: AnteConfig | None = None,
) -> dict:
    """Split a daily budget into cue-anchored micro-sessions (the 'time back'
    shape). Each slot gets a share of the budget proportional to its minutes."""
    cfg = cfg or CONFIG
    slots = slots or [("morning", 30), ("during the day", 30), ("night", 15)]
    slot_total = sum(m for _, m in slots) or 1
    capacity = int(budget_minutes * 60 / cfg.seconds_per_card)
    remaining = min(capacity, due_count)
    plan = []
    for name, mins in slots:
        scaled = round(budget_minutes * mins / slot_total)
        slot_cap = int(scaled * 60 / cfg.seconds_per_card)
        take = min(slot_cap, remaining)
        remaining -= take
        plan.append({"slot": name, "minutes": scaled, "cards": take})
    return {
        "budget_minutes": budget_minutes,
        "daily_capacity_cards": capacity,
        "due_count": due_count,
        "covers_due_load": capacity >= due_count,
        "slots": plan,
    }
