# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Open-ended (short-answer) application items + offline grading.

Multiple choice proves recognition; an open-ended answer proves you can *produce*
the reasoning. Per the product brief, mastery is shown from quizzes AND open-ended
questions, so these feed the same application-accuracy signal that gates mastery.

Grading is offline-first (the app must score with AI switched off): a student's
answer is scored 0..1 against the item's rubric points and keywords via token /
phrase overlap, returning partial credit plus what was matched vs. missed so the
feedback is corrective (Guskey's feedback loop), not just a grade. An AI grader
can layer on top when a key is present, but is never required.

Pure logic; unit-testable without Anki. Degrades gracefully (empty) if the
generated ``data/open_ended_items.json`` is absent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import CONFIG, AnteConfig
from .memory import wilson_interval
from .performance_items import REASSESS_AFTER_DAYS, SECONDS_PER_DAY

OPEN_DATA_PATH = Path(__file__).with_name("data") / "open_ended_items.json"
MIN_ITEMS_FOR_EVIDENCE = 1

_STOP = frozenset(
    "the a an and or of to in on for is are be by with as it its that this than then "
    "which who whom whose what when where why how into from at via about not no can "
    "will would should could may might do does done has have had you your they their "
    "these those there here more most less least each both any all some such because "
    "if but so we our i me my he she his her them also very much many one two".split()
)


@dataclass(frozen=True)
class OpenItem:
    id: str
    topic: str
    prompt: str
    model_answer: str
    rubric_points: tuple[str, ...]
    keywords: tuple[str, ...]
    difficulty: float


@dataclass(frozen=True)
class OpenAttempt:
    score: float
    ts: float = 0.0
    confidence: float | None = None
    elapsed_ms: int | None = None


@dataclass(frozen=True)
class GradeResult:
    score: float
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    feedback: str

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "matched": list(self.matched),
            "missing": list(self.missing),
            "feedback": self.feedback,
        }


@lru_cache(maxsize=2)
def load_open_items(path: str | None = None) -> tuple[OpenItem, ...]:
    p = Path(path or OPEN_DATA_PATH)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return ()
    out: list[OpenItem] = []
    for topic, items in raw.get("items", {}).items():
        for it in items:
            out.append(
                OpenItem(
                    id=it["id"],
                    topic=topic,
                    prompt=it["prompt"],
                    model_answer=it.get("model_answer", ""),
                    rubric_points=tuple(it.get("rubric_points", [])),
                    keywords=tuple(k.lower() for k in it.get("keywords", [])),
                    difficulty=float(it.get("difficulty", 0.5)),
                )
            )
    return tuple(out)


def open_items_by_topic(path: str | None = None) -> dict[str, list[OpenItem]]:
    out: dict[str, list[OpenItem]] = {}
    for it in load_open_items(path):
        out.setdefault(it.topic, []).append(it)
    return out


def open_item_by_id(item_id: str) -> OpenItem | None:
    return next((it for it in load_open_items() if it.id == item_id), None)


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP}


def _salient(point: str) -> set[str]:
    return {w for w in _tokens(point) if len(w) >= 4}


def grade_open_answer(answer: str, item: OpenItem) -> GradeResult:
    """Score a free-text answer 0..1 against the item's keywords + rubric points.

    Keyword phrases are matched as substrings; a rubric point counts as hit when
    at least half its salient words appear. Very short answers are capped so you
    can't pass by pasting a single keyword."""
    ans = (answer or "").strip().lower()
    ans_tokens = _tokens(ans)
    if not ans_tokens:
        return GradeResult(0.0, (), item.rubric_points, "No answer to grade yet.")

    # keyword coverage (phrases as substrings, words as tokens)
    kw = list(item.keywords)
    kw_hits = [k for k in kw if (k in ans if " " in k else k in ans_tokens)]
    kw_frac = len(kw_hits) / len(kw) if kw else 0.0

    # rubric coverage
    matched: list[str] = []
    missing: list[str] = []
    for pt in item.rubric_points:
        sal = _salient(pt)
        if not sal:
            continue
        hit = len(sal & ans_tokens) / len(sal)
        (matched if hit >= 0.5 else missing).append(pt)
    rub_total = len(matched) + len(missing)
    rub_frac = len(matched) / rub_total if rub_total else kw_frac

    score = 0.5 * rub_frac + 0.5 * kw_frac
    # brevity guard: an answer under ~8 meaningful words can't earn full marks
    if len(ans_tokens) < 8:
        score = min(score, 0.5)
    score = round(max(0.0, min(1.0, score)), 3)

    if score >= 0.8:
        fb = "Strong — you hit the key ideas."
    elif score >= CONFIG.open_pass_score:
        fb = "Solid, but tighten it up."
    else:
        fb = "Not yet — compare with the model answer."
    if missing:
        fb += " Missing: " + "; ".join(missing[:3]) + "."
    return GradeResult(score, tuple(matched), tuple(missing), fb)


def _coerce_open(value: object) -> list[OpenAttempt]:
    out: list[OpenAttempt] = []
    seq = value if isinstance(value, (list, tuple)) else [value]
    for e in seq:
        if isinstance(e, dict):
            out.append(
                OpenAttempt(
                    float(e.get("s", e.get("score", 0.0))),
                    float(e.get("t", e.get("ts", 0.0))),
                    _num(e.get("conf", e.get("confidence"))),
                    _num_int(e.get("ms", e.get("elapsed_ms"))),
                )
            )
        elif isinstance(e, (list, tuple)) and e:
            out.append(
                OpenAttempt(
                    float(e[0]),
                    float(e[1]) if len(e) > 1 else 0.0,
                    _num(e[2]) if len(e) > 2 else None,
                    _num_int(e[3]) if len(e) > 3 else None,
                )
            )
        elif isinstance(e, (int, float)) and not isinstance(e, bool):
            out.append(OpenAttempt(float(e)))
    return out


def _num(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _num_int(v: object) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def normalize_open_log(responses: Mapping[str, object]) -> dict[str, list[OpenAttempt]]:
    out: dict[str, list[OpenAttempt]] = {}
    for k, v in (responses or {}).items():
        attempts = _coerce_open(v)
        if attempts:
            out[str(k)] = attempts
    return out


def _latest(attempts: list[OpenAttempt]) -> OpenAttempt:
    return sorted(attempts, key=lambda a: a.ts)[-1]


def topic_open_counts(
    responses: Mapping[str, object],
) -> dict[str, tuple[float, int]]:
    """Per-topic (score_sum, n) from the most recent attempt on each answered
    open-ended item. score_sum is fractional (partial credit)."""
    log = normalize_open_log(responses)
    by_topic: dict[str, list[float]] = {}
    for it in load_open_items():
        attempts = log.get(it.id)
        if attempts:
            by_topic.setdefault(it.topic, []).append(_latest(attempts).score)
    return {t: (sum(s), len(s)) for t, s in by_topic.items()}


def topic_open_accuracy(
    responses: Mapping[str, object],
) -> dict[str, tuple[float, float, float]]:
    out: dict[str, tuple[float, float, float]] = {}
    for topic, (s, n) in topic_open_counts(responses).items():
        if n < MIN_ITEMS_FOR_EVIDENCE:
            continue
        point = s / n
        lo, hi = wilson_interval(round(point * n), n)
        out[topic] = (point, lo, hi)
    return out


def _open_state(
    attempts: list[OpenAttempt],
    now: float | None,
    reassess_days: float,
    pass_score: float,
) -> str:
    if not attempts:
        return "new"
    latest = _latest(attempts)
    if latest.score < pass_score:
        return "wrong"
    if now is not None and (now - latest.ts) >= reassess_days * SECONDS_PER_DAY:
        return "stale"
    return "ok"


def due_open_items(
    responses: Mapping[str, object],
    now: float | None = None,
    prefer_topic: str | None = None,
    reassess_days: float = REASSESS_AFTER_DAYS,
    cfg: AnteConfig | None = None,
) -> list[OpenItem]:
    cfg = cfg or CONFIG
    log = normalize_open_log(responses)
    new_i: list[OpenItem] = []
    wrong_i: list[OpenItem] = []
    stale_i: list[OpenItem] = []
    for it in load_open_items():
        st = _open_state(log.get(it.id, []), now, reassess_days, cfg.open_pass_score)
        if st == "new":
            new_i.append(it)
        elif st == "wrong":
            wrong_i.append(it)
        elif st == "stale":
            stale_i.append(it)
    order = new_i + wrong_i + stale_i
    if prefer_topic:
        order.sort(key=lambda it: 0 if it.topic == prefer_topic else 1)
    return order


def next_open_item(
    responses: Mapping[str, object],
    prefer_topic: str | None = None,
    now: float | None = None,
) -> OpenItem | None:
    due = due_open_items(responses, now=now, prefer_topic=prefer_topic)
    return due[0] if due else None


def open_progress(
    responses: Mapping[str, object],
    now: float | None = None,
    reassess_days: float = REASSESS_AFTER_DAYS,
    cfg: AnteConfig | None = None,
) -> dict:
    """Counts for the open-ended pool (mirrors performance_items.quiz_progress)."""
    cfg = cfg or CONFIG
    log = normalize_open_log(responses)
    total = attempted = proven = due = 0
    next_stale: float | None = None
    for it in load_open_items():
        total += 1
        attempts = log.get(it.id, [])
        if attempts:
            attempted += 1
        st = _open_state(attempts, now, reassess_days, cfg.open_pass_score)
        if st == "ok":
            proven += 1
            if now is not None:
                remaining = _latest(attempts).ts + reassess_days * SECONDS_PER_DAY - now
                if next_stale is None or remaining < next_stale:
                    next_stale = remaining
        else:
            due += 1
    return {
        "total": total,
        "attempted": attempted,
        "proven": proven,
        "due": due,
        "next_reassess_days": (
            max(0.0, next_stale) / SECONDS_PER_DAY if next_stale is not None else None
        ),
    }
