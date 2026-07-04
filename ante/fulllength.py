# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Full-length practice tests (2 per plan), assembled from the item bank.

Two condensed full-lengths, MCAT-shaped: every section in real order
(C/P -> CARS -> B/B -> P/S), question counts proportional to the real exam,
and real per-question pacing (~95s). The two forms are DISJOINT — no question
appears on both — so the second test measures growth, not memory of the first.

Timing follows AAMC guidance: the first full-length lands about a month out
(end of Build/Bridge), the second ~10 days out (Sharpen dress rehearsal), both
anchored to the exam date like the phase arc.

Scoring mirrors the real scale: each section maps to 118-132, the total to
472-528. It's labelled condensed and heuristic — honest, like readiness.

Pure logic; unit-tested without Anki.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .performance_items import PerfItem, load_items

# real MCAT section order
SECTION_ORDER = ["chem_phys", "cars", "bio_biochem", "psych_soc"]
SECTION_NAMES = {
    "chem_phys": "Chemical & Physical Foundations",
    "cars": "Critical Analysis & Reasoning",
    "bio_biochem": "Biological & Biochemical Foundations",
    "psych_soc": "Psychological & Social Foundations",
}
# questions per section per form (proportioned to the real 59/53/59/59,
# bounded by the bank so BOTH forms stay disjoint)
SECTION_QUOTA = {"chem_phys": 20, "cars": 7, "bio_biochem": 20, "psych_soc": 16}
SECONDS_PER_QUESTION = 95
_SPLIT_SEED = 20260703  # fixed: forms are stable across runs

FL_TESTS = (1, 2)


@dataclass(frozen=True)
class FLSection:
    id: str
    name: str
    items: tuple[PerfItem, ...]

    @property
    def minutes(self) -> int:
        return max(1, round(len(self.items) * SECONDS_PER_QUESTION / 60))


def _split_bank(items: list[PerfItem] | None = None) -> dict[int, list[FLSection]]:
    """Deterministically split the bank into two disjoint forms."""
    items = items if items is not None else list(load_items())
    by_sec: dict[str, list[PerfItem]] = {}
    for it in items:
        sec = it.topic.split("::")[1] if "::" in it.topic else it.topic
        by_sec.setdefault(sec, []).append(it)
    forms: dict[int, list[FLSection]] = {1: [], 2: []}
    rng = random.Random(_SPLIT_SEED)
    for sec in SECTION_ORDER:
        pool = sorted(by_sec.get(sec, []), key=lambda i: i.id)
        rng.shuffle(pool)
        quota = min(SECTION_QUOTA.get(sec, 15), len(pool) // 2)
        forms[1].append(FLSection(sec, SECTION_NAMES[sec], tuple(pool[:quota])))
        forms[2].append(FLSection(sec, SECTION_NAMES[sec], tuple(pool[quota : 2 * quota])))
    return forms


def build_full_length(test_no: int, items: list[PerfItem] | None = None) -> dict:
    """The form for test 1 or 2, payload-ready."""
    test_no = 1 if int(test_no) not in FL_TESTS else int(test_no)
    sections = _split_bank(items)[test_no]
    return {
        "ok": True,
        "test_no": test_no,
        "sections": [
            {
                "id": s.id,
                "name": s.name,
                "minutes": s.minutes,
                "items": [
                    {
                        "id": it.id,
                        "stem": it.stem,
                        "options": list(it.choices),
                        "correct_index": it.correct_index,
                        "topic": it.topic,
                    }
                    for it in s.items
                ],
            }
            for s in sections
        ],
        "total_questions": sum(len(s.items) for s in sections),
        "total_minutes": sum(s.minutes for s in sections),
        "seconds_per_question": SECONDS_PER_QUESTION,
    }


def score_full_length(
    answers: dict[str, int], test_no: int, items: list[PerfItem] | None = None
) -> dict:
    """Score answers {item_id: chosen_index}. Unanswered counts wrong (like
    the real exam's 'no penalty for guessing' framing — blanks earn nothing)."""
    sections = _split_bank(items)[1 if int(test_no) not in FL_TESTS else int(test_no)]
    out_secs = []
    total = 0
    for s in sections:
        n = len(s.items)
        correct = sum(
            1 for it in s.items if answers.get(it.id) is not None and int(answers[it.id]) == it.correct_index
        )
        scaled = 118 + round(14 * (correct / n)) if n else 118
        total += scaled
        out_secs.append(
            {"id": s.id, "name": s.name, "n": n, "correct": correct, "scaled": scaled}
        )
    return {"test_no": int(test_no), "sections": out_secs, "total": total}


def fl_offsets(days_remaining: int) -> dict[int, int]:
    """Calendar offsets (days from today) for the two full-lengths, anchored to
    the exam: FL1 ~a month out, FL2 ~10 days out. On a short runway they clamp
    to today (take the baseline immediately) instead of landing in the past."""
    days = max(1, int(days_remaining))
    off1 = max(0, days - 32)
    off2 = max(off1, days - 10)
    return {1: off1, 2: off2}
