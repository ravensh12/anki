# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Circuit — Ante's world model. The app is a card den, not a dashboard.

You open Ante seated in the Emerald Room, a members-only card den somewhere
above Canal Street, where Sahir — a djinn who has dealt cards since Babylon —
deals your due cards onto green felt. Your opponent is the House: the
forgetting curve. The mastery map is a world tour, THE CIRCUIT: each exam
section is a city, every topic is a table in that city, and the engine's
honest signals decide each table's state:

    roped     -> locked behind prerequisite tables (velvet rope up)
    open      -> unlocked, being played (you have a seat)
    lowtable  -> corrective (application dipped; sent back to the low table)
    won       -> mastered (the brass plaque is yours)
    unlisted  -> no cards, no evidence — the table isn't on the card.
                 Abstention as geography: the Circuit refuses to list what
                 it cannot support (Principle 4).

Dust = memory decay: 1 - average FSRS recall over the table's cards, so a won
table physically goes cold under dust as retrievability drops between reviews —
the forgetting curve as the House quietly reclaiming the room.

One rule survives from every previous incarnation of this app, because
Principle 2 demands it: THE SEAT. At every moment exactly one chair is pulled
out, pointing at the single best next action. A world without it is a decision
generator; with it, the next correct action stays the path of least resistance.

Pure logic; deterministic (a table's stake and position derive from the
outline), so the Circuit is stable across reloads and identical for a given
collection state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .mastery import MasteryStatus, TopicMastery
from .outline import Outline, load_outline

WORLD_NAME = "The Circuit"

# The tour's stop order: home den first, then out into the world. Sections
# not listed here (a different exam outline) are appended in outline order.
CITY_ORDER = ("chem_phys", "cars", "bio_biochem", "psych_soc")

# Section id -> (city, the room's name, flavor line, backdrop asset id).
CITY_FLAVOR: dict[str, tuple[str, str, str, str]] = {
    "chem_phys": (
        "New York",
        "The Emerald Room",
        "brass lamps, rain on glass, and clean arithmetic",
        "city_new_york",
    ),
    "cars": (
        "Monte Carlo",
        "Salon Bleu",
        "read the table, not the cards",
        "city_monte_carlo",
    ),
    "bio_biochem": (
        "Havana",
        "Casa Verde",
        "living systems and warm night air",
        "city_havana",
    ),
    "psych_soc": (
        "Macau",
        "The Jade House",
        "people, and the reasons they play",
        "city_macau",
    ),
}

TABLE_ROPED = "roped"
TABLE_OPEN = "open"
TABLE_LOW = "lowtable"
TABLE_WON = "won"
TABLE_UNLISTED = "unlisted"

LEGEND = [
    {"state": TABLE_WON, "means": "won — proven by application; the plaque is yours"},
    {"state": TABLE_OPEN, "means": "open — you have a seat; not yet proven"},
    {"state": TABLE_LOW, "means": "the low table — application dipped; win your way back"},
    {"state": TABLE_ROPED, "means": "roped off — beat its prerequisite tables first"},
    {"state": TABLE_UNLISTED, "means": "unlisted — no evidence; the Circuit won't pretend"},
    {"state": "dust", "means": "dust settles as recall fades — play the table to clear it"},
]


def _table_state(m: TopicMastery) -> str:
    if m.status == MasteryStatus.MASTERED:
        return TABLE_WON
    if m.cards_total == 0:
        return TABLE_UNLISTED
    if m.status == MasteryStatus.CORRECTIVE:
        return TABLE_LOW
    if m.status == MasteryStatus.LOCKED:
        return TABLE_ROPED
    return TABLE_OPEN


def _night_phase(now_hour: int, ritual: Mapping | None) -> dict:
    """The den's windows. Dawn holds behind the glass until the morning game is
    kept; the neon comes up with the midnight game — the ritual is the room's
    day cycle, not a checklist row."""
    r = ritual or {}
    morning_done = bool((r.get("morning") or {}).get("done"))
    night_done = bool((r.get("night") or {}).get("done"))
    if 5 <= now_hour < 12:
        phase = "day" if morning_done else "dawn"
    elif 12 <= now_hour < 17:
        phase = "day"
    elif 17 <= now_hour < 22:
        phase = "night" if night_done else "dusk"
    else:
        phase = "night"
    return {
        "phase": phase,
        "first_light_kept": morning_done,
        "last_light_kept": night_done,
        "headline": str(r.get("headline", "")),
    }


def _chips(weight: float, max_weight: float) -> int:
    """A table's stake as a 1..5 chip stack (relative exam value)."""
    if max_weight <= 0:
        return 1
    return max(1, min(5, round(5 * weight / max_weight)))


def build_world(  # noqa: PLR0913
    mastery: Mapping[str, TopicMastery],
    *,
    ritual: Mapping | None = None,
    readiness: Mapping | None = None,
    due_count: int = 0,
    due_by_topic: Mapping[str, int] | None = None,
    best_next_topic: str | None = None,
    diagnostic_taken: bool = True,
    palace_counts: Mapping[str, int] | None = None,
    palace_total: int = 0,
    viva_suggested: list[dict] | None = None,
    dreamseed_ready: bool = False,
    documentary_ready: bool = False,
    exam_days_left: int | None = None,
    now_hour: int = 12,
    outline: Outline | None = None,
) -> dict:
    """Assemble the world model the den renders from: the room's hour, the
    Circuit's cities and tables, and the one pulled-out seat."""
    outline = outline or load_outline()
    due_by_topic = dict(due_by_topic or {})
    palace_counts = dict(palace_counts or {})
    viva_suggested = viva_suggested or []
    readiness = dict(readiness or {})

    max_weight = max(
        (m.exam_weight for m in mastery.values()), default=1.0
    ) or 1.0

    cities = []
    counts = {
        s: 0 for s in (TABLE_WON, TABLE_OPEN, TABLE_LOW, TABLE_ROPED, TABLE_UNLISTED)
    }
    table_city: dict[str, str] = {}
    order = {sid: i for i, sid in enumerate(CITY_ORDER)}
    sections = sorted(
        outline.sections, key=lambda s: order.get(s.id, len(order) + 1)
    )
    for i, section in enumerate(sections):
        city, room, flavor, asset = CITY_FLAVOR.get(
            section.id, (section.name, f"The {section.code} Room", section.name, "")
        )
        tables: list[dict[str, Any]] = []
        for t in section.topic_objs:
            m = mastery.get(t.tag)
            if m is None:
                continue
            state = _table_state(m)
            counts[state] += 1
            table_city[t.tag] = city
            dust = None
            if state != TABLE_UNLISTED:
                dust = round(max(0.0, min(1.0, 1.0 - m.average_recall)), 3)
            tables.append(
                {
                    "tag": m.tag,
                    "name": m.name,
                    "state": state,
                    "dust": dust,
                    "heat": round(m.normalized_mastery, 3),
                    "weight": round(m.exam_weight, 3),
                    "chips": _chips(m.exam_weight, max_weight),
                    "cards": m.cards_total,
                    "due": int(due_by_topic.get(m.tag, 0)),
                    "vault": int(palace_counts.get(m.tag, 0)),
                    "reason": m.reasons[0] if m.reasons else "",
                }
            )
        # stake order: biggest tables first, like a real card room's floor
        tables.sort(key=lambda t: -t["weight"])
        won = sum(1 for t in tables if t["state"] == TABLE_WON)
        listed = sum(1 for t in tables if t["state"] != TABLE_UNLISTED)
        cities.append(
            {
                "id": section.id,
                "code": section.code,
                "section_name": section.name,
                "city": city,
                "room": room,
                "flavor": flavor,
                "asset": asset,
                "stop": i + 1,
                "won": won,
                "listed": listed,
                "total": len(tables),
                "beaten": listed > 0 and won == listed,
                "tables": tables,
            }
        )

    seat = _seat(
        ritual or {},
        due_count=due_count,
        best_next_topic=best_next_topic,
        diagnostic_taken=diagnostic_taken,
        viva_suggested=viva_suggested,
        dreamseed_ready=dreamseed_ready,
        documentary_ready=documentary_ready,
        table_city=table_city,
        mastery=mastery,
        now_hour=now_hour,
    )

    return {
        "name": WORLD_NAME,
        "night": _night_phase(now_hour, ritual),
        "exam_days_left": exam_days_left,
        "cities": cities,
        "cities_beaten": sum(1 for c in cities if c["beaten"]),
        "seat": seat,
        "counts": counts,
        "legend": LEGEND,
    }


def _seat(
    ritual: Mapping,
    *,
    due_count: int,
    best_next_topic: str | None,
    diagnostic_taken: bool,
    viva_suggested: list[dict],
    dreamseed_ready: bool,
    documentary_ready: bool,
    table_city: Mapping[str, str],
    mastery: Mapping[str, TopicMastery],
    now_hour: int,
) -> dict:
    """Exactly one pulled-out chair. Priorities, in order of leverage:

    buy-in -> keep the current bookend (a session at the biggest-stakes
    table) -> the premiere -> heads-up with Sahir -> clear the remaining
    due stack -> the last hand replayed -> check the Book.
    """

    def table_of(tag: str | None) -> tuple[str, str, str] | None:
        if not tag or tag not in mastery:
            return None
        m = mastery[tag]
        return tag, m.name, table_city.get(tag, "")

    if not diagnostic_taken:
        return {
            "kind": "buyin",
            "label": "Play the buy-in game",
            "reason": "every honest game starts from a measured stack",
        }

    next_bookend = ritual.get("next")
    target = table_of(best_next_topic)
    if next_bookend and due_count > 0 and target:
        tag, name, city = target
        game = "morning game" if next_bookend == "first_light" else "midnight game"
        return {
            "kind": "session",
            "table": tag,
            "city": city,
            "label": f"The {game} — {due_count} cards on the felt",
            "reason": f"highest stakes right now: {name}",
        }

    if documentary_ready:
        return {
            "kind": "premiere",
            "label": "Tonight: The Run premieres",
            "reason": "the whole climb, cut together — watch it before you sleep",
        }

    if viva_suggested:
        top = viva_suggested[0]
        return {
            "kind": "headsup",
            "topic": top["topic"],
            "label": f"Heads-up with Sahir: {top['name']}",
            "reason": "closest to the bar — push your chips in and explain it straight",
        }

    if due_count > 0 and target:
        tag, name, city = target
        return {
            "kind": "session",
            "table": tag,
            "city": city,
            "label": f"{due_count} cards on the felt",
            "reason": f"start at {name} — the order spends minutes where they pay",
        }

    if dreamseed_ready and now_hour >= 19:
        return {
            "kind": "reel",
            "label": "The last hand, replayed",
            "reason": "tonight's hardest cards — watch, then sleep on them",
        }

    return {
        "kind": "book",
        "label": "Check the Book",
        "reason": "the day is banked — go see the honest line",
    }
