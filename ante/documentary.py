# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Documentary — the exam-eve montage of the student's real climb.

The night before the exam is when panic-cramming wins and confidence quietly
dies. The Documentary counters it with evidence: a ~3 minute cut of the
student's own logged arc — the day-one baseline, the work, the seals, the
honest verdict — narrated calmly and built entirely from data and assets that
already exist (charts render locally; scenes come from the Palace cache).
Self-efficacy from *mastery experiences* is the strongest source there is
(Bandura 1977); this is a mastery-experience showreel.

Honesty rules hold on the last night too: the verdict chapter shows the same
range/abstention readiness shows — the film never promises a score.

Pure logic: selects, scripts, and orders chapters; the UI sequences it like the
intro film; the Studio pre-voices narration (persona: chronicler).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PREMIERE_WINDOW_DAYS = 1  # exam eve and exam day


def build_documentary(
    *,
    exam_days_left: int | None,
    diagnostic: Mapping | None,
    readiness: Mapping | None,
    streak: Mapping | None,
    n_reviews: int,
    active_days: int,
    topics_mastered: int,
    viva_passed: int = 0,
    palace_records: list[Mapping] | None = None,
    baseline_total: int | None = None,
    force: bool = False,
) -> dict:
    """Script the premiere. Available on exam eve (or forced for preview)."""
    ready = force or (
        exam_days_left is not None and 0 <= exam_days_left <= PREMIERE_WINDOW_DAYS
    )
    if not ready:
        return {
            "available": False,
            "reason": "premieres on exam eve — keep playing",
            "days_left": exam_days_left,
        }

    readiness = dict(readiness or {})
    diagnostic = dict(diagnostic or {})
    palace_records = list(palace_records or [])
    streak = dict(streak or {})

    chapters: list[dict] = []

    # I. where you started
    taken = bool(diagnostic.get("taken"))
    if taken and baseline_total:
        opening_line = (
            f"Night one, you played the buy-in game cold. It said {baseline_total}. "
            "You wrote it down and kept your seat. That was the whole trick."
        )
    else:
        opening_line = (
            "Night one, there was no stack — just the decision to count it honestly."
        )
    chapters.append(
        {
            "id": "baseline",
            "title": "I. The Buy-In",
            "line": opening_line,
            "visual": {"kind": "stat", "value": baseline_total, "label": "night-one baseline"},
        }
    )

    # II. the grind (evidence of effort, never 'hours studied')
    best = int(
        streak.get("longest_streak", streak.get("best", streak.get("current", 0)))
        or 0
    )
    chapters.append(
        {
            "id": "work",
            "title": "II. The Grind",
            "line": (
                f"{n_reviews} honest retrievals across {active_days} days at the "
                "felt. "
                + (
                    f"Your longest run: {best} nights kept."
                    if best
                    else "Not perfect. Kept anyway."
                )
            ),
            "visual": {"kind": "stat", "value": n_reviews, "label": "retrievals logged"},
        }
    )

    # III. the plaques (mastery experiences — Bandura's strongest source)
    seal_line = (
        f"{topics_mastered} tables won"
        + (f", {viva_passed} of them heads-up, out loud" if viva_passed else "")
        + ". Not covered — proven."
    )
    chapters.append(
        {
            "id": "seals",
            "title": "III. The Plaques",
            "line": seal_line,
            "visual": {"kind": "stat", "value": topics_mastered, "label": "tables won"},
        }
    )

    # IV. the vault (their strangest, most personal artifact)
    if palace_records:
        pick = max(palace_records, key=lambda r: r.get("created_at", 0))
        chapters.append(
            {
                "id": "palace",
                "title": "IV. The Vault",
                "line": (
                    f"{len(palace_records)} scenes commissioned for the cards "
                    "that fought back. This one included."
                ),
                "visual": {
                    "kind": "scene",
                    "still": pick.get("still"),
                    "motion": pick.get("motion"),
                    "caption": pick.get("caption"),
                },
            }
        )

    # V. the line — the same honesty as the Book, no exceptions tonight
    abstained = bool(readiness.get("abstained", True))
    visual: dict[str, Any]
    if abstained:
        verdict_line = (
            "The Book never learned to flatter you, and it won't start "
            "tonight: not enough evidence for a line. What it can say is that "
            "you did the work in the right order."
        )
        visual = {"kind": "stat", "value": None, "label": "no line without evidence"}
    else:
        lo, hi = (readiness.get("total_range") or [None, None])[:2]
        verdict_line = (
            f"Walking in somewhere between {lo} and {hi}. That line was earned "
            "one honest retrieval at a time — trust it the way it trusted you."
        )
        visual = {
            "kind": "range",
            "value": readiness.get("projected_total"),
            "range": [lo, hi],
            "label": "tomorrow's honest line",
        }
    chapters.append(
        {"id": "verdict", "title": "V. The Line", "line": verdict_line, "visual": visual}
    )

    # VI. the send-off
    chapters.append(
        {
            "id": "sendoff",
            "title": "VI. The Final Table",
            "line": (
                "Tomorrow, one more morning game — then walk in and answer like "
                "you've answered ten thousand times already. Because you have."
            ),
            "visual": {"kind": "sunrise"},
        }
    )

    return {
        "available": True,
        "title": "The Run",
        "persona": "chronicler",
        "chapters": chapters,
        "seconds_per_chapter": 22,
    }


def narration_texts(doc: Mapping) -> list[str]:
    if not doc.get("available"):
        return []
    return [str(c["line"]) for c in doc.get("chapters", [])]
