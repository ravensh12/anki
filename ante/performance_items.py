# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Application/transfer items and how they gate mastery (PRD 4.1, 7.2).

The reviewer's central push: the MCAT is reasoning, so mastery must gate on
*application*, not recall. A topic's ``perf_accuracy`` therefore comes from the
student's accuracy on these scenario items (does knowing the fact let you USE it
on a new question?), and that feeds both the mastery gate and readiness.

Bloom's mastery loop (not a one-shot test): every answer is logged as an
attempt ``{item_id: [[choice, ts], ...]}``. A topic's accuracy is scored from
the *most recent* attempt on each item, so a wrong item can be re-proven after
corrective study (the gate reopens) and a mastered item resurfaces for spaced
re-assessment once it goes stale. An item is "due" when it has never been tried,
was last answered wrong, or is a correct answer older than the re-assessment
window. The legacy ``{item_id: choice}`` shape is still accepted and normalized.

Pure logic + data so it is unit-testable without Anki.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .memory import wilson_interval

DATA_PATH = Path(__file__).with_name("data") / "performance_items.json"
# an item counts toward a topic's perf accuracy; a topic needs at least this
# many answered application items before it contributes real evidence.
MIN_ITEMS_FOR_EVIDENCE = 1
# a correct application item resurfaces for spaced re-assessment after this many
# days (Bloom's loop keeps mastery earned, not permanent). Tunable.
REASSESS_AFTER_DAYS = 21.0
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class PerfItem:
    id: str
    topic: str
    stem: str
    choices: tuple[str, ...]
    correct_index: int
    paraphrase_of: str
    difficulty: float


@dataclass(frozen=True)
class Attempt:
    """One recorded answer to an application item: chosen index, epoch time,
    the student's self-rated confidence 0..1 (for metacognition), and how long
    they took in ms (for the fluent/careless/effortful classification)."""

    choice: int
    ts: float = 0.0
    confidence: float | None = None
    elapsed_ms: int | None = None


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _opt_int(value: object) -> int | None:
    f = _opt_float(value)
    return int(f) if f is not None else None


def _attempt_from_obj(obj: dict) -> Attempt:
    choice = obj.get("c", obj.get("choice", obj.get("i", 0)))
    ts = obj.get("t", obj.get("ts", 0.0))
    conf = _opt_float(obj.get("conf", obj.get("confidence")))
    ms = _opt_int(obj.get("ms", obj.get("elapsed_ms")))
    return Attempt(int(choice), float(ts), conf, ms)


def _coerce_attempts(value: object) -> list[Attempt]:
    """Accept legacy (int), 2-tuple ([choice, ts]) or 3-tuple ([choice, ts,
    confidence]) response shapes."""
    if isinstance(value, bool):  # guard: bool is a subclass of int
        return []
    if isinstance(value, int):
        return [Attempt(int(value), 0.0)]
    if isinstance(value, dict):
        return [_attempt_from_obj(value)]
    if isinstance(value, (list, tuple)):
        out: list[Attempt] = []
        for e in value:
            if isinstance(e, bool):
                continue
            if isinstance(e, int):
                out.append(Attempt(int(e), 0.0))
            elif isinstance(e, dict):
                out.append(_attempt_from_obj(e))
            elif isinstance(e, (list, tuple)) and e:
                ts = float(e[1]) if len(e) > 1 else 0.0
                conf = _opt_float(e[2]) if len(e) > 2 else None
                ms = _opt_int(e[3]) if len(e) > 3 else None
                out.append(Attempt(int(e[0]), ts, conf, ms))
        return out
    return []


def normalize_log(responses: Mapping[str, object]) -> dict[str, list[Attempt]]:
    """Normalize any stored response shape into {item_id: [Attempt, ...]}."""
    out: dict[str, list[Attempt]] = {}
    for k, v in (responses or {}).items():
        attempts = _coerce_attempts(v)
        if attempts:
            out[str(k)] = attempts
    return out


def _latest(attempts: list[Attempt]) -> Attempt:
    """Most recent attempt (highest ts; ties resolve to the last appended)."""
    return sorted(attempts, key=lambda a: a.ts)[-1]


def _item_state(
    item: PerfItem,
    attempts: list[Attempt],
    now: float | None,
    reassess_days: float,
) -> str:
    """One of: 'new' (never tried), 'wrong' (last answer incorrect),
    'stale' (correct but past the re-assessment window), 'ok' (proven & fresh)."""
    if not attempts:
        return "new"
    latest = _latest(attempts)
    if latest.choice != item.correct_index:
        return "wrong"
    if now is not None and (now - latest.ts) >= reassess_days * SECONDS_PER_DAY:
        return "stale"
    return "ok"


@lru_cache(maxsize=2)
def load_items(path: str | None = None) -> tuple[PerfItem, ...]:
    raw = json.loads(Path(path or DATA_PATH).read_text(encoding="utf-8"))
    out: list[PerfItem] = []
    for topic, items in raw["items"].items():
        for it in items:
            out.append(
                PerfItem(
                    id=it["id"],
                    topic=topic,
                    stem=it["stem"],
                    choices=tuple(it["choices"]),
                    correct_index=int(it["correct_index"]),
                    paraphrase_of=it.get("paraphrase_of", ""),
                    difficulty=float(it.get("difficulty", 0.5)),
                )
            )
    return tuple(out)


def items_by_topic(path: str | None = None) -> dict[str, list[PerfItem]]:
    out: dict[str, list[PerfItem]] = {}
    for it in load_items(path):
        out.setdefault(it.topic, []).append(it)
    return out


def item_by_id(item_id: str) -> PerfItem | None:
    return next((it for it in load_items() if it.id == item_id), None)


def is_correct(item_id: str, chosen_index: int) -> bool:
    it = item_by_id(item_id)
    return bool(it and chosen_index == it.correct_index)


def topic_application_accuracy(
    responses: Mapping[str, object],
) -> dict[str, tuple[float, float, float]]:
    """Per-topic (point, low, high) accuracy scored from the MOST RECENT attempt
    on each answered item (so re-tests update mastery; Bloom's loop).

    Only topics with >= MIN_ITEMS_FOR_EVIDENCE answered items are returned, so
    mastery/readiness stay honest about topics without transfer evidence."""
    log = normalize_log(responses)
    by_topic: dict[str, list[int]] = {}
    for it in load_items():
        attempts = log.get(it.id)
        if attempts:
            latest = _latest(attempts)
            by_topic.setdefault(it.topic, []).append(
                1 if latest.choice == it.correct_index else 0
            )
    out: dict[str, tuple[float, float, float]] = {}
    for topic, outcomes in by_topic.items():
        n = len(outcomes)
        if n < MIN_ITEMS_FOR_EVIDENCE:
            continue
        correct = sum(outcomes)
        lo, hi = wilson_interval(correct, n)
        out[topic] = (correct / n, lo, hi)
    return out


def topic_application_counts(
    responses: Mapping[str, object],
) -> dict[str, tuple[int, int]]:
    """Per-topic (correct, n) from the most recent attempt on each answered
    multiple-choice item, so open-ended evidence can be pooled with it."""
    log = normalize_log(responses)
    counts: dict[str, list[int]] = {}
    for it in load_items():
        attempts = log.get(it.id)
        if attempts:
            latest = _latest(attempts)
            c = counts.setdefault(it.topic, [0, 0])
            c[0] += 1 if latest.choice == it.correct_index else 0
            c[1] += 1
    return {t: (c, n) for t, (c, n) in counts.items()}


def due_items(
    responses: Mapping[str, object],
    now: float | None = None,
    prefer_topic: str | None = None,
    reassess_days: float = REASSESS_AFTER_DAYS,
) -> list[PerfItem]:
    """Items that need (re)assessment now, ordered: never-tried, then failed
    (corrective re-test), then stale-correct (spaced re-assessment). A given
    topic can be floated to the front. ``now=None`` disables staleness so pure
    tests are deterministic."""
    log = normalize_log(responses)
    new_items: list[PerfItem] = []
    wrong_items: list[PerfItem] = []
    stale_items: list[PerfItem] = []
    for it in load_items():
        state = _item_state(it, log.get(it.id, []), now, reassess_days)
        if state == "new":
            new_items.append(it)
        elif state == "wrong":
            wrong_items.append(it)
        elif state == "stale":
            stale_items.append(it)
    order = new_items + wrong_items + stale_items
    if prefer_topic:
        # stable sort keeps the new/wrong/stale priority within each topic group
        order.sort(key=lambda it: 0 if it.topic == prefer_topic else 1)
    return order


def next_item(
    responses: Mapping[str, object],
    prefer_topic: str | None = None,
    now: float | None = None,
) -> PerfItem | None:
    """Next item due for (re)assessment, preferring a given topic (e.g. the
    weakest one). Returns None only when nothing is due."""
    due = due_items(responses, now=now, prefer_topic=prefer_topic)
    return due[0] if due else None


def quiz_progress(
    responses: Mapping[str, object],
    now: float | None = None,
    reassess_days: float = REASSESS_AFTER_DAYS,
) -> dict:
    """Counts for the quiz UI: total items, how many attempted, how many are
    currently proven (last answer correct & fresh), how many are due, and days
    until the next proven item goes stale for re-assessment."""
    log = normalize_log(responses)
    total = attempted = proven = due = 0
    next_stale_secs: float | None = None
    for it in load_items():
        total += 1
        attempts = log.get(it.id, [])
        if attempts:
            attempted += 1
        state = _item_state(it, attempts, now, reassess_days)
        if state == "ok":
            proven += 1
            if now is not None:
                remaining = _latest(attempts).ts + reassess_days * SECONDS_PER_DAY - now
                if next_stale_secs is None or remaining < next_stale_secs:
                    next_stale_secs = remaining
        else:
            due += 1
    return {
        "total": total,
        "attempted": attempted,
        "proven": proven,
        "due": due,
        "next_reassess_days": (
            max(0.0, next_stale_secs) / SECONDS_PER_DAY
            if next_stale_secs is not None
            else None
        ),
    }


@dataclass(frozen=True)
class ParaphraseRow:
    topic: str
    card_recall: float
    application_accuracy: float

    @property
    def gap(self) -> float:
        return self.card_recall - self.application_accuracy


def paraphrase_gaps(
    responses: Mapping[str, object], topic_recall: Mapping[str, float]
) -> list[ParaphraseRow]:
    """The paraphrase test (PRD 7d): for each topic with both a recall number
    and application items answered, report recall minus application accuracy. A
    large positive gap = memorizing wording but failing transfer."""
    app = topic_application_accuracy(responses)
    rows = []
    for topic, (acc, _lo, _hi) in app.items():
        if topic in topic_recall:
            rows.append(ParaphraseRow(topic, topic_recall[topic], acc))
    return rows
