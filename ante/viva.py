# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Viva — an oral board examination to seal topic mastery (Principle 3).

Recognition is the weakest proof of knowledge; *production* is the strongest
(generation effect, Slamecka & Graf 1978; self-explanation, Chi 1994; the
Feynman technique). The Viva makes the test-out path (`test_out_enabled`, until
now a dormant config guardrail) a real examination: the student explains a
topic in their own words — spoken aloud (Studio.transcribe) or typed — and an
examiner probes exactly the rubric points their explanation missed, the way a
real attending would.

Grading is the existing offline rubric machinery (openended.grade_open_answer):
partial credit per rubric point, fully deterministic, works with AI off. The
LLM's only job is phrasing a sharper follow-up probe; a template fallback keeps
the whole examination offline-capable. Because a viva is graded through the
same open-response log as every other application item, passing one feeds
mastery, comprehension, and readiness with zero new scoring machinery.

The examiner draws your full answer out of you across rounds — so the final
grade rescores everything you said, concatenated. Elaboration counts; the bar
(viva_pass_score) stays above the ordinary pass bar because a seal should mean
something.

Pure logic; the Qt layer persists sessions and appends finished records to the
open-response log.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .config import CONFIG, AnteConfig
from .mastery import MasteryStatus, TopicMastery
from .openended import (
    OpenItem,
    due_open_items,
    grade_open_answer,
    load_open_items,
)
from .outline import load_outline

OPEN_STATUS = "open"
PASSED = "passed"
FAILED = "failed"


# --------------------------------------------------------------------------- #
# eligibility — which topics may be defended
# --------------------------------------------------------------------------- #


def eligible_topics(
    mastery: Mapping[str, TopicMastery],
    open_responses: Mapping[str, object] | None = None,
    cfg: AnteConfig | None = None,
    limit: int = 5,
) -> list[dict]:
    """Topics ready to be defended, closest to the bar first.

    A viva is a test-out: any unlocked, unmastered topic with an open-ended
    item can be defended. Corrective topics are listed too — defending one is
    the fastest honest way out of the corrective loop.
    """
    cfg = cfg or CONFIG
    has_items = {it.topic for it in load_open_items()}
    out: list[dict[str, Any]] = []
    for m in mastery.values():
        if m.status not in (MasteryStatus.ACTIVE, MasteryStatus.CORRECTIVE):
            continue
        if m.tag not in has_items:
            continue
        gap = (
            cfg.mastery_bar - m.perf_accuracy
            if m.perf_accuracy is not None
            else 1.0
        )
        out.append(
            {
                "topic": m.tag,
                "name": m.name,
                "section": m.section_id,
                "status": m.status.value,
                "accuracy": m.perf_accuracy,
                "gap": round(max(0.0, gap), 4),
                "weight": m.exam_weight,
            }
        )
    out.sort(key=lambda d: (d["gap"], -d["weight"]))
    return out[:limit]


# --------------------------------------------------------------------------- #
# the examination
# --------------------------------------------------------------------------- #


def _pick_item(
    topic: str, open_responses: Mapping[str, object] | None, now: float | None
) -> OpenItem | None:
    due = due_open_items(open_responses or {}, now=now, prefer_topic=topic)
    for it in due:
        if it.topic == topic:
            return it
    # nothing due: reuse any item for the topic — a viva may re-examine
    return next((it for it in load_open_items() if it.topic == topic), None)


def opening_line(topic_name: str) -> str:
    return (
        f"Your chips are in the middle, so take a breath. Explain {topic_name} "
        "to me as if I'm a sharp player who has never seen the game — mechanism "
        "first, then why it matters. The table is yours."
    )


def start_viva(
    topic: str,
    open_responses: Mapping[str, object] | None = None,
    now: float | None = None,
    cfg: AnteConfig | None = None,
) -> dict | None:
    """Open an examination session for a topic. None if no item bank exists."""
    cfg = cfg or CONFIG
    now = now or time.time()
    item = _pick_item(topic, open_responses, now)
    if item is None:
        return None
    t = load_outline().topic(topic)
    name = t.name if t else topic.rsplit("::", 1)[-1].replace("_", " ")
    return {
        "id": f"viva-{int(now)}-{abs(hash(topic)) % 9999:04d}",
        "topic": topic,
        "topic_name": name,
        "item_id": item.id,
        "question": item.prompt,
        "opening": opening_line(name),
        "rounds": [],
        "max_rounds": 1 + cfg.viva_probe_rounds,
        "status": OPEN_STATUS,
        "started_at": now,
        "finished_at": None,
        "final_score": None,
        "verdict": None,
    }


_PROBE_SYSTEM = (
    "You are Sahir, a warm, exacting card dealer running a heads-up oral "
    "defense. The student's explanation missed a rubric point. Ask ONE "
    "Socratic follow-up question (<=25 words) that leads them toward exactly "
    "that point without giving it away. Return only the question."
)


def _probe_question(
    item: OpenItem, missing: tuple[str, ...], answer: str, provider=None
) -> str:
    """One follow-up aimed at the weakest missed rubric point."""
    target = missing[0] if missing else (item.rubric_points[0] if item.rubric_points else "")
    if provider is not None and hasattr(provider, "complete"):
        try:
            q = provider.complete(
                _PROBE_SYSTEM,
                f"Question: {item.prompt}\nStudent said: {answer}\n"
                f"Missed rubric point: {target}",
                max_tokens=80,
            ).strip()
            if 0 < len(q) <= 220:
                return q
        except Exception:
            pass
    if target:
        return (
            f"Good — but there's one card you haven't turned over: {target}. "
            "Walk me through that part."
        )
    return "Go deeper — what's the mechanism underneath what you just said?"


def submit_answer(
    session: dict,
    answer: str,
    provider=None,
    now: float | None = None,
    cfg: AnteConfig | None = None,
) -> dict:
    """Grade one spoken/typed turn; probe again or close with a verdict."""
    cfg = cfg or CONFIG
    now = now or time.time()
    if session.get("status") != OPEN_STATUS:
        return session
    item = _item_of(session)
    if item is None:
        session["status"] = FAILED
        session["verdict"] = {"passed": False, "line": "The item bank moved — start again."}
        return session

    grade = grade_open_answer(answer or "", item)
    rounds = list(session.get("rounds", []))
    rounds.append(
        {
            "question": session["question"] if not rounds else rounds[-1]["probe"],
            "answer": answer or "",
            "score": grade.score,
            "matched": list(grade.matched),
            "missing": list(grade.missing),
            "feedback": grade.feedback,
            "at": now,
        }
    )
    session["rounds"] = rounds

    # everything said so far, graded as one cumulative explanation
    cumulative = grade_open_answer(" ".join(r["answer"] for r in rounds), item)
    session["cumulative_score"] = cumulative.score

    done_probing = len(rounds) >= session.get("max_rounds", 3)
    nothing_missing = not cumulative.missing
    if done_probing or (nothing_missing and cumulative.score >= cfg.viva_pass_score):
        return _close(session, item, cumulative, now, cfg)

    probe = _probe_question(item, cumulative.missing, answer or "", provider)
    rounds[-1]["probe"] = probe
    session["ask"] = probe
    return session


def _close(session: dict, item: OpenItem, cumulative, now: float, cfg: AnteConfig) -> dict:
    passed = cumulative.score >= cfg.viva_pass_score
    session["status"] = PASSED if passed else FAILED
    session["finished_at"] = now
    session["final_score"] = cumulative.score
    session["ask"] = None
    name = session.get("topic_name", "the topic")
    if passed:
        line = (
            f"The table is yours. You didn't recognize {name} — you *rebuilt* it "
            "out loud, heads-up. That's the difference the exam pays for."
        )
    else:
        missing = "; ".join(cumulative.missing[:2]) or "the core mechanism"
        line = (
            f"Not yet — and that's information, not a sentence. You're missing "
            f"{missing}. Study exactly that, then come take your seat again."
        )
    session["verdict"] = {
        "passed": passed,
        "score": cumulative.score,
        "bar": cfg.viva_pass_score,
        "line": line,
        "missing": list(cumulative.missing),
        "matched": list(cumulative.matched),
        "model_answer": item.model_answer,
    }
    return session


def _item_of(session: dict) -> OpenItem | None:
    from .openended import open_item_by_id

    return open_item_by_id(str(session.get("item_id", "")))


# --------------------------------------------------------------------------- #
# feeding the honest machinery
# --------------------------------------------------------------------------- #


def records_for_log(session: dict) -> list[tuple[str, float, float]]:
    """(item_id, score, ts) entries for the open-response log — a finished viva
    is application evidence like any other, so it feeds mastery/readiness
    through the exact same pipe."""
    if session.get("status") not in (PASSED, FAILED):
        return []
    return [
        (
            str(session["item_id"]),
            float(session.get("final_score") or 0.0),
            float(session.get("finished_at") or time.time()),
        )
    ]


def already_examined_today(
    viva_log: list[dict] | None, topic: str, now: float | None = None
) -> bool:
    """One defense per topic per day — a failed viva routes to study, not to
    immediate re-tries (the corrective loop, not a slot machine)."""
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    for s in viva_log or []:
        if s.get("topic") != topic or not s.get("finished_at"):
            continue
        if time.strftime("%Y-%m-%d", time.localtime(float(s["finished_at"]))) == day:
            return True
    return False


# --------------------------------------------------------------------------- #
# the dealer's presence (Studio specs — generated once, cached forever)
# --------------------------------------------------------------------------- #

DEALER_SPEC = {
    "prompt": (
        "Portrait of Sahir, an ageless djinn card dealer in a charcoal "
        "three-piece suit, faint smoke curling from his cuffs, kind exacting "
        "amber eyes, seated at a green-felt card table under a brass lamp, "
        "hands folded on the felt, looking directly at the viewer"
    ),
    "title": "Sahir",
    "caption": "The dealer is listening.",
}

VERDICT_SPECS = {
    "passed": {
        "prompt": (
            "Sahir the djinn dealer pushing a small brass plaque across green "
            "felt toward the viewer, warm proud restrained smile, smoke "
            "curling from his cuffs in the lamplight"
        ),
        "title": "The Plaque",
        "caption": "Defended out loud, heads-up — the table is yours.",
        "motion": "smoke curls, lamplight sways, a slow satisfied nod",
    },
    "failed": {
        "prompt": (
            "Sahir the djinn dealer gathering the cards in one smooth motion, "
            "expression kind and unbowed, gesturing toward the reading lamp "
            "and the books beyond the felt"
        ),
        "title": "Not Yet",
        "caption": "Not yet — study exactly this, then take your seat again.",
        "motion": "cards gathered in one smooth motion, lamplight breathing",
    },
}


def verdict_speech_text(session: dict) -> str:
    v = session.get("verdict") or {}
    return str(v.get("line", ""))
