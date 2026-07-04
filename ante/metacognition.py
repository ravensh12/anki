# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Confidence calibration: does the student know what they know?

Metacognitive calibration \u2014 the match between how confident you feel and how
often you're actually right \u2014 is one of the better-supported predictors of exam
performance, and it is exactly the honesty thesis turned on the student. When a
learner answers an application item, they also rate their confidence; this module
scores the gap.

  * Brier score: mean squared error between stated confidence and correctness
    (0 = perfect, lower is better).
  * Bias: mean(confidence) \u2212 mean(correct). Positive = overconfident (the
    dangerous direction for the MCAT), negative = underconfident.
  * Per-section breakdown + a reliability table (predicted vs actual by band).

Consumes the same attempt log as performance_items (attempts that carry a
confidence value). Pure logic; unit-tested without Anki.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .config import CONFIG, AnteConfig
from .openended import load_open_items, normalize_open_log
from .performance_items import load_items, normalize_log

# minimum confidence-rated answers before we report a calibration reading
MIN_RATED = 5

# a per-section confidence bias of this magnitude maps to the full penalty
_FULL_PENALTY_BIAS = 0.30

# reliability-diagram bands
_BANDS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)]


@dataclass(frozen=True)
class _Point:
    section: str
    confidence: float
    correct: int


def _section_of(topic: str) -> str:
    parts = topic.split("::")
    return parts[1] if len(parts) > 2 else topic


def _collect(
    responses: Mapping[str, object],
    open_responses: Mapping[str, object] | None = None,
    cfg: AnteConfig | None = None,
) -> list[_Point]:
    """Every confidence-rated attempt (multiple-choice AND open-ended), with its
    section + correctness. For open-ended, "correct" = graded score >= pass."""
    cfg = cfg or CONFIG
    log = normalize_log(responses)
    by_id = {it.id: it for it in load_items()}
    pts: list[_Point] = []
    for item_id, attempts in log.items():
        it = by_id.get(item_id)
        if it is None:
            continue
        section = _section_of(it.topic)
        for a in attempts:
            if a.confidence is None:
                continue
            conf = min(1.0, max(0.0, a.confidence))
            pts.append(_Point(section, conf, 1 if a.choice == it.correct_index else 0))

    if open_responses:
        olog = normalize_open_log(open_responses)
        oby = {oi.id: oi for oi in load_open_items()}
        for oid, o_attempts in olog.items():
            oitem = oby.get(oid)
            if oitem is None:
                continue
            osection = _section_of(oitem.topic)
            for oa in o_attempts:
                if oa.confidence is None:
                    continue
                oconf = min(1.0, max(0.0, oa.confidence))
                ocorrect = 1 if oa.score >= cfg.open_pass_score else 0
                pts.append(_Point(osection, oconf, ocorrect))
    return pts


def _brier(points: list[_Point]) -> float:
    return sum((p.confidence - p.correct) ** 2 for p in points) / len(points)


def _bias(points: list[_Point]) -> float:
    n = len(points)
    return sum(p.confidence for p in points) / n - sum(p.correct for p in points) / n


def _verdict(bias: float) -> str:
    if bias > 0.10:
        return "overconfident"
    if bias < -0.10:
        return "underconfident"
    return "well calibrated"


def _report_from_points(
    points: list[_Point], min_rated: int, no_data_reason: str, source: str = ""
) -> dict:
    """Turn confidence-vs-correctness points into a calibration reading, or an
    honest abstention when there isn't enough rated data yet."""
    n = len(points)
    if n < min_rated:
        return {
            "available": False,
            "reason": no_data_reason.format(n=n, need=min_rated),
            "n": n,
            "source": source,
        }

    brier = _brier(points)
    bias = _bias(points)
    # friendly 0..100 score: 100 at Brier 0, ~0 by Brier 0.35 (worse than a coin)
    score = max(0, min(100, round(100 * (1 - brier / 0.35))))

    per_section: list[dict] = []
    by_section: dict[str, list[_Point]] = {}
    for p in points:
        by_section.setdefault(p.section, []).append(p)
    for sec, pts in by_section.items():
        per_section.append(
            {
                "section": sec,
                "n": len(pts),
                "brier": round(_brier(pts), 4),
                "bias": round(_bias(pts), 4),
                "avg_confidence": round(sum(x.confidence for x in pts) / len(pts), 4),
                "accuracy": round(sum(x.correct for x in pts) / len(pts), 4),
                "verdict": _verdict(_bias(pts)),
            }
        )
    per_section.sort(key=lambda s: abs(s["bias"]), reverse=True)

    bins: list[dict] = []
    for lo, hi in _BANDS:
        band = [p for p in points if lo <= p.confidence < hi]
        if not band:
            continue
        bins.append(
            {
                "lo": lo,
                "hi": min(1.0, hi),
                "predicted": round(sum(p.confidence for p in band) / len(band), 4),
                "actual": round(sum(p.correct for p in band) / len(band), 4),
                "n": len(band),
            }
        )

    worst = per_section[0] if per_section else None
    return {
        "available": True,
        "n": n,
        "source": source,
        "brier": round(brier, 4),
        "bias": round(bias, 4),
        "score": score,
        "verdict": _verdict(bias),
        "avg_confidence": round(sum(p.confidence for p in points) / n, 4),
        "accuracy": round(sum(p.correct for p in points) / n, 4),
        "per_section": per_section,
        "worst_section": worst["section"] if worst else None,
        "bins": bins,
    }


_QUIZ_NO_DATA = (
    "only {n} confidence-rated answers (need {need}) \u2014 rate your confidence on "
    "the quiz to unlock calibration"
)
_FLASH_NO_DATA = (
    "only {n} rated flashcards (need {need}) \u2014 say how sure you are before you "
    "flip to unlock calibration"
)
_ALL_NO_DATA = (
    "only {n} confidence-rated answers (need {need}) \u2014 keep studying to unlock "
    "calibration"
)


def calibration_report(
    responses: Mapping[str, object],
    min_rated: int = MIN_RATED,
    open_responses: Mapping[str, object] | None = None,
) -> dict:
    """Application (quiz + open-ended) calibration, or an honest abstention."""
    points = _collect(responses, open_responses)
    return _report_from_points(points, min_rated, _QUIZ_NO_DATA, source="application")


def _flash_points(flash_log: list | None) -> list[_Point]:
    """Points from the flashcard confidence log: each record carries the
    student's pre-flip confidence and whether they actually recalled it
    (ease >= Good). Records may be dicts or ``[conf, correct, ts, topic, ms]``."""
    pts: list[_Point] = []
    for rec in flash_log or []:
        conf = correct = None
        section = ""
        if isinstance(rec, dict):
            conf = rec.get("conf", rec.get("confidence"))
            correct = rec.get("correct")
            section = rec.get("section") or rec.get("topic") or ""
        elif isinstance(rec, (list, tuple)) and len(rec) >= 2:
            conf, correct = rec[0], rec[1]
            section = rec[3] if len(rec) > 3 and rec[3] else ""
        if conf is None or correct is None:
            continue
        try:
            c = min(1.0, max(0.0, float(conf)))
        except (TypeError, ValueError):
            continue
        sec = _section_of(str(section)) if section else "all"
        pts.append(_Point(sec, c, 1 if correct else 0))
    return pts


def flashcard_calibration(flash_log: list | None, min_rated: int = MIN_RATED) -> dict:
    """Calibration on FLASHCARDS: did 'I know this' before flipping match whether
    you actually recalled it? This is the familiarity-illusion detector."""
    return _report_from_points(
        _flash_points(flash_log), min_rated, _FLASH_NO_DATA, source="flashcard"
    )


def combined_calibration(
    responses: Mapping[str, object],
    open_responses: Mapping[str, object] | None = None,
    flash_log: list | None = None,
    min_rated: int = MIN_RATED,
) -> dict:
    """Calibration across everything (flashcards + quiz + open-ended) — the single
    self-trust signal that drives the honest-interval penalty."""
    points = _collect(responses, open_responses) + _flash_points(flash_log)
    return _report_from_points(points, min_rated, _ALL_NO_DATA, source="combined")


def calibration_comparison(flash: Mapping[str, Any], quiz: Mapping[str, Any]) -> dict:
    """Flashcards vs quiz: is confidence inflated by familiarity? A positive gap
    (more overconfident on cards than on the quiz) is the illusion of competence
    (Roediger & Karpicke): the quiz is the honest mirror."""
    if not (flash.get("available") and quiz.get("available")):
        return {"available": False}
    fb = float(flash.get("bias", 0.0) or 0.0)
    qb = float(quiz.get("bias", 0.0) or 0.0)
    gap = round(fb - qb, 4)
    if gap > 0.08:
        headline = "Your flashcards feel more solid than they are."
        detail = (
            f"You're about {round(gap * 100)}% more overconfident on flashcards than "
            "on the quiz \u2014 the classic familiarity trap. 'I've seen this' is not "
            "'I can use this'; trust the quiz over the feeling."
        )
    elif gap < -0.08:
        headline = "You sell yourself short on the quiz."
        detail = (
            f"You're better calibrated on flashcards; on the quiz you're about "
            f"{round(-gap * 100)}% more under-confident. You know more under "
            "pressure than you give yourself credit for."
        )
    else:
        headline = "Your confidence travels honestly from recall to application."
        detail = (
            "Flashcard and quiz calibration line up \u2014 your sense of what you know "
            "holds up when the question is reworded."
        )
    return {
        "available": True,
        "gap": gap,
        "flash_bias": round(fb, 4),
        "quiz_bias": round(qb, 4),
        "headline": headline,
        "detail": detail,
    }


def overconfidence_penalty(
    calibration: Mapping[str, Any], cfg: AnteConfig | None = None
) -> float:
    """How much to *lower and widen* honest estimates because the student is
    systematically over-confident (says "sure", gets it wrong).

    This is the mechanism behind "confident-but-wrong -> the interval goes down":
    a positive confidence bias erodes trust in the student's self-assessment, so
    readiness and comprehension lower bounds drop. Returns an accuracy-point
    penalty in [0, calibration_penalty_max]; 0 when calibrated or under-confident.
    """
    cfg = cfg or CONFIG
    if not calibration.get("available"):
        return 0.0
    bias = float(calibration.get("bias", 0.0) or 0.0)
    if bias <= 0:
        return 0.0
    scaled = cfg.calibration_penalty_max * (bias / _FULL_PENALTY_BIAS)
    return round(min(cfg.calibration_penalty_max, scaled), 4)


def section_overconfidence(
    calibration: Mapping[str, Any], cfg: AnteConfig | None = None
) -> dict[str, float]:
    """Per-section overconfidence penalties (same scaling as the global one), for
    widening each topic's comprehension band by the section it belongs to."""
    cfg = cfg or CONFIG
    out: dict[str, float] = {}
    if not calibration.get("available"):
        return out
    for s in calibration.get("per_section", []) or []:
        bias = float(s.get("bias", 0.0) or 0.0)
        if bias > 0:
            scaled = cfg.calibration_penalty_max * (bias / _FULL_PENALTY_BIAS)
            out[str(s.get("section"))] = round(
                min(cfg.calibration_penalty_max, scaled), 4
            )
    return out


def self_trust(calibration: Mapping[str, Any]) -> dict:
    """A compact "can you trust your own sense of readiness?" reading for the UI:
    the calibration score plus a plain verdict and the direction of any bias."""
    if not calibration.get("available"):
        return {"available": False, "reason": calibration.get("reason", "")}
    bias = float(calibration.get("bias", 0.0) or 0.0)
    return {
        "available": True,
        "score": calibration.get("score"),
        "verdict": calibration.get("verdict"),
        "direction": "overconfident"
        if bias > 0
        else ("underconfident" if bias < 0 else "even"),
        "n": calibration.get("n"),
    }
