# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Baseline Diagnostic — Bloom's formative pre-assessment, at onboarding.

Mastery learning starts with a formative check, not a syllabus: before the plan
exists, measure where the student actually stands. The diagnostic samples a
fixed number of application items per MCAT section (mostly multiple-choice, a
couple open-ended for production evidence), spread across the highest-weight
topics, and turns the answers into an honest baseline:

  * per-section accuracy with a Wilson interval (small n → wide band, shown);
  * a baseline score projection using the same documented heuristic map as
    readiness (118 + 14*accuracy per section) — labelled a snapshot, never a
    verdict;
  * the weakest section + topics, which seed the plan's starting priorities.

Answers are recorded through the SAME response log as the quiz, so diagnostic
evidence immediately feeds mastery, comprehension, calibration and readiness —
the diagnostic is the first formative loop, not a separate silo. Pure logic;
unit-tested without Anki.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar

from .config import CONFIG, AnteConfig
from .memory import wilson_interval
from .openended import normalize_open_log, open_items_by_topic
from .outline import Outline, load_outline
from .performance_items import items_by_topic, normalize_log
from .readiness import accuracy_to_section_score

# Form shape: ~10 questions per section, production (open-ended) mixed in.
DEFAULT_MCQ_PER_SECTION = 8
DEFAULT_OPEN_PER_SECTION = 2
# A section needs at least this many answered items before we read it at all.
MIN_SECTION_EVIDENCE = 4
# Deterministic sampling so a student's form is stable across reloads.
DEFAULT_SEED = 20260701


@dataclass(frozen=True)
class DiagnosticSection:
    id: str
    code: str
    name: str
    items: tuple[dict, ...]


@dataclass(frozen=True)
class DiagnosticForm:
    sections: tuple[DiagnosticSection, ...]

    @property
    def item_ids(self) -> list[str]:
        return [it["id"] for s in self.sections for it in s.items]

    @property
    def total(self) -> int:
        return sum(len(s.items) for s in self.sections)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "item_ids": self.item_ids,
            "sections": [
                {
                    "id": s.id,
                    "code": s.code,
                    "name": s.name,
                    "count": len(s.items),
                    "items": list(s.items),
                }
                for s in self.sections
            ],
        }


def _mcq_payload(it) -> dict:
    return {
        "id": it.id,
        "type": "mcq",
        "topic": it.topic,
        "stem": it.stem,
        "choices": list(it.choices),
        "correct_index": it.correct_index,
        "difficulty": it.difficulty,
    }


def _open_payload(it) -> dict:
    return {
        "id": it.id,
        "type": "open",
        "topic": it.topic,
        "stem": it.prompt,
        "difficulty": it.difficulty,
    }


def _sample_round_robin(pools: list[list], want: int, rng: random.Random) -> list:
    """Take up to ``want`` items, one per topic pool per pass (highest exam
    weight first), so the sample spreads across a section's topics instead of
    exhausting one. Pools are consumed in place."""
    out: list = []
    while len(out) < want and any(pools):
        for pool in pools:
            if len(out) >= want:
                break
            if pool:
                out.append(pool.pop(rng.randrange(len(pool))))
    return out


def build_diagnostic(
    mcq_per_section: int = DEFAULT_MCQ_PER_SECTION,
    open_per_section: int = DEFAULT_OPEN_PER_SECTION,
    outline: Outline | None = None,
    seed: int = DEFAULT_SEED,
) -> DiagnosticForm:
    """Assemble the diagnostic form: per section, ``mcq_per_section`` MCQ +
    ``open_per_section`` open-ended, sampled across topics by exam weight.
    Sections appear in outline (test-day) order; open-ended items are spread
    through the section rather than stacked at the end."""
    outline = outline or load_outline()
    rng = random.Random(seed)
    mcq_by_topic = items_by_topic()
    open_by_topic = open_items_by_topic()

    sections: list[DiagnosticSection] = []
    for sec in outline.sections:
        topics = sorted(sec.topic_objs, key=lambda t: t.exam_weight, reverse=True)
        mcq_pools = [list(mcq_by_topic.get(t.tag, [])) for t in topics]
        open_pools = [list(open_by_topic.get(t.tag, [])) for t in topics]

        mcqs = [
            _mcq_payload(it)
            for it in _sample_round_robin(mcq_pools, mcq_per_section, rng)
        ]
        opens = [
            _open_payload(it)
            for it in _sample_round_robin(open_pools, open_per_section, rng)
        ]

        items = list(mcqs)
        if items and opens:
            # spread open-ended through the section (e.g. positions ~1/3, ~2/3)
            step = max(1, (len(items) + len(opens)) // (len(opens) + 1))
            for i, op in enumerate(opens):
                items.insert(min(len(items), step * (i + 1) + i), op)
        else:
            items.extend(opens)

        if items:
            sections.append(
                DiagnosticSection(
                    id=sec.id, code=sec.code, name=sec.name, items=tuple(items)
                )
            )
    return DiagnosticForm(sections=tuple(sections))


# --------------------------------------------------------------------------- #
# Summary: answers -> per-section baseline + plan seeds
# --------------------------------------------------------------------------- #


_A = TypeVar("_A")


def _latest(attempts: list[_A]) -> _A:
    return sorted(attempts, key=lambda a: a.ts)[-1]  # type: ignore[attr-defined]


def summarize_diagnostic(
    item_ids: list[str],
    mcq_responses: Mapping[str, object],
    open_responses: Mapping[str, object],
    outline: Outline | None = None,
    cfg: AnteConfig | None = None,
    min_section_evidence: int = MIN_SECTION_EVIDENCE,
) -> dict:
    """Turn recorded answers to the diagnostic's items into the honest baseline.

    Pools MCQ correctness (0/1 from the latest attempt) with open-ended partial
    credit (0..1 score) per section, exactly like the mastery signal, then maps
    accuracy to the section scale with a Wilson band. A section with fewer than
    ``min_section_evidence`` answers is reported but not scored; the overall
    baseline appears only when every section is scoreable — partial diagnostics
    stay partial instead of extrapolating.
    """
    outline = outline or load_outline()
    cfg = cfg or CONFIG
    wanted = set(item_ids)

    mcq_log = {k: v for k, v in normalize_log(mcq_responses).items() if k in wanted}
    open_log = {
        k: v for k, v in normalize_open_log(open_responses).items() if k in wanted
    }

    mcq_items = {
        it["id"]: it
        for topic_items in _diagnostic_mcq_index().values()
        for it in topic_items
    }

    # successes/attempts pooled per section and per topic
    sec_success: dict[str, float] = {}
    sec_n: dict[str, int] = {}
    topic_success: dict[str, float] = {}
    topic_n: dict[str, int] = {}
    confidences: list[tuple[float, float]] = []  # (confidence, outcome 0..1)

    def _credit(section_id: str, topic: str, value: float, conf) -> None:
        sec_success[section_id] = sec_success.get(section_id, 0.0) + value
        sec_n[section_id] = sec_n.get(section_id, 0) + 1
        topic_success[topic] = topic_success.get(topic, 0.0) + value
        topic_n[topic] = topic_n.get(topic, 0) + 1
        if conf is not None:
            confidences.append((float(conf), value))

    for iid, attempts in mcq_log.items():
        meta = mcq_items.get(iid)
        if not meta:
            continue
        sec = outline.section_of(meta["topic"])
        if not sec:
            continue
        latest = _latest(attempts)
        _credit(
            sec.id,
            meta["topic"],
            1.0 if latest.choice == meta["correct_index"] else 0.0,
            latest.confidence,
        )

    open_topics = {
        it.id: it.topic for items in open_items_by_topic().values() for it in items
    }
    for iid, o_attempts in open_log.items():
        topic = open_topics.get(iid)
        if not topic:
            continue
        sec = outline.section_of(topic)
        if not sec:
            continue
        o_latest = _latest(o_attempts)
        _credit(sec.id, topic, max(0.0, min(1.0, o_latest.score)), o_latest.confidence)

    # per-section rows in outline order
    sections: list[dict] = []
    all_scored = True
    for sec in outline.sections:
        n = sec_n.get(sec.id, 0)
        row: dict = {
            "id": sec.id,
            "code": sec.code,
            "name": sec.name,
            "answered": n,
        }
        if n >= min_section_evidence:
            acc = sec_success[sec.id] / n
            lo, hi = wilson_interval(sec_success[sec.id], n)
            row.update(
                {
                    "scored": True,
                    "accuracy": round(acc, 3),
                    "band": [round(lo, 3), round(hi, 3)],
                    "score": accuracy_to_section_score(acc),
                    "score_range": [
                        accuracy_to_section_score(lo),
                        accuracy_to_section_score(hi),
                    ],
                }
            )
        else:
            all_scored = False
            row.update(
                {
                    "scored": False,
                    "reason": (
                        f"only {n} answered (need {min_section_evidence}) — "
                        "no reading for this section"
                    ),
                }
            )
        sections.append(row)

    scored_rows = [s for s in sections if s.get("scored")]
    answered_total = sum(s["answered"] for s in sections)

    baseline_total = baseline_range = None
    if all_scored and scored_rows:
        baseline_total = sum(s["score"] for s in scored_rows)
        baseline_range = [
            sum(s["score_range"][0] for s in scored_rows),
            sum(s["score_range"][1] for s in scored_rows),
        ]

    weakest_section = min(scored_rows, key=lambda s: s["accuracy"], default=None)
    strongest_section = max(scored_rows, key=lambda s: s["accuracy"], default=None)

    # weakest topics (any evidence counts here — it seeds priorities, not scores)
    _topic_acc = [(t, topic_success[t] / topic_n[t], topic_n[t]) for t in topic_n]
    _topic_acc.sort(key=lambda r: (r[1], -r[2]))
    topic_rows = [
        {"topic": t, "accuracy": round(acc, 3), "n": n} for t, acc, n in _topic_acc
    ]

    # a small calibration snapshot: did confidence match reality on day one?
    calib = None
    if len(confidences) >= 5:
        avg_conf = sum(c for c, _ in confidences) / len(confidences)
        avg_acc = sum(o for _, o in confidences) / len(confidences)
        calib = {
            "avg_confidence": round(avg_conf, 3),
            "accuracy": round(avg_acc, 3),
            "bias": round(avg_conf - avg_acc, 3),
        }

    if baseline_total is not None:
        headline = (
            f"Baseline: {baseline_total} "
            f"({baseline_range[0]}–{baseline_range[1]}) from {answered_total} items"
        )
    elif answered_total:
        headline = (
            f"{answered_total} items answered — not enough per section for a "
            "baseline score, but every answer already feeds your map."
        )
    else:
        headline = "Diagnostic not taken."

    return {
        "available": answered_total > 0,
        "answered": answered_total,
        "sections": sections,
        "baseline_total": baseline_total,
        "baseline_range": baseline_range,
        "weakest_section": weakest_section["id"] if weakest_section else None,
        "strongest_section": strongest_section["id"] if strongest_section else None,
        "weakest_topics": topic_rows[:3],
        "calibration": calib,
        "headline": headline,
        "method": (
            "Pooled MCQ correctness + open-ended partial credit per section; "
            "Wilson interval; section score = 118 + 14*accuracy. A snapshot "
            "from a small sample — the band is the honest part."
        ),
    }


def _diagnostic_mcq_index() -> dict[str, list[dict]]:
    return {
        topic: [_mcq_payload(it) for it in items]
        for topic, items in items_by_topic().items()
    }
