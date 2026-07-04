# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Complete-comprehension model — the data behind the Comprehension Atlas.

This answers the product's headline question: "show me my COMPLETE comprehension
of the whole MCAT, and how much I can trust it." For every topic it reports the
application accuracy (quiz + open-ended — never flashcard self-ratings) as a
comprehension point with a confidence band, and it *widens/lowers that band by the
student's overconfidence* so a topic they feel sure about but keep missing reads
as genuinely uncertain, not falsely green.

The overall reading is the exam-weighted aggregate over topics with evidence, and
it is always paired with how much of the exam's weight actually has evidence, so
the number is honest about what it does and doesn't cover.

Pure logic; unit-tested without Anki.
"""

from __future__ import annotations

from collections.abc import Mapping

from .config import CONFIG, AnteConfig
from .mastery import TopicMastery
from .metacognition import section_overconfidence
from .outline import Outline, load_outline

Perf = Mapping[str, tuple[float, float, float]]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def build_comprehension(
    mastery: dict[str, TopicMastery],
    topic_perf: Perf | None,
    calibration: Mapping[str, object] | None = None,
    outline: Outline | None = None,
    cfg: AnteConfig | None = None,
) -> dict:
    """Per-topic + overall comprehension with calibration-adjusted bands."""
    outline = outline or load_outline()
    cfg = cfg or CONFIG
    topic_perf = topic_perf or {}
    sec_oc = section_overconfidence(calibration or {}, cfg)

    topics_out: list[dict] = []
    # accumulators for the overall exam-weighted aggregate over evidenced topics
    wsum = 0.0
    p_acc = lo_acc = hi_acc = 0.0
    evidenced_weight = 0.0
    total_weight = 0.0
    n_evidence = 0

    section_weight = {s.id: s.weight for s in outline.sections}

    for section in outline.sections:
        for t in section.topic_objs:
            m = mastery.get(t.tag)
            if m is None:
                continue
            weight = section_weight.get(section.id, 1.0) * t.exam_weight
            total_weight += weight
            oc = sec_oc.get(section.id, 0.0)
            perf = topic_perf.get(t.tag)
            if perf is not None:
                p, lo, hi = perf
                p_adj = _clamp(p - 0.5 * oc)
                lo_adj = _clamp(lo - oc)
                hi_adj = _clamp(hi)
                topics_out.append(
                    {
                        "tag": t.tag,
                        "name": t.name,
                        "section": section.id,
                        "status": m.status.value,
                        "has_evidence": True,
                        "comprehension": round(p_adj, 4),
                        "band": [round(lo_adj, 4), round(hi_adj, 4)],
                        "raw_accuracy": round(p, 4),
                        "overconfidence": round(oc, 4),
                        "retention": round(m.strength_fraction, 4),
                        "exam_weight": round(weight, 4),
                    }
                )
                wsum += weight
                p_acc += weight * p_adj
                lo_acc += weight * lo_adj
                hi_acc += weight * hi_adj
                evidenced_weight += weight
                n_evidence += 1
            else:
                topics_out.append(
                    {
                        "tag": t.tag,
                        "name": t.name,
                        "section": section.id,
                        "status": m.status.value,
                        "has_evidence": False,
                        "comprehension": None,
                        "band": None,
                        "raw_accuracy": None,
                        "overconfidence": round(oc, 4),
                        "retention": round(m.strength_fraction, 4),
                        "exam_weight": round(weight, 4),
                    }
                )

    if wsum > 0:
        overall = {
            "available": True,
            "comprehension": round(p_acc / wsum, 4),
            "band": [round(lo_acc / wsum, 4), round(hi_acc / wsum, 4)],
            "evidenced_weight_fraction": round(evidenced_weight / total_weight, 4)
            if total_weight
            else 0.0,
            "n_evidence_topics": n_evidence,
        }
    else:
        overall = {
            "available": False,
            "reason": "no quiz or open-ended evidence yet — answer application "
            "items to map your comprehension",
            "evidenced_weight_fraction": 0.0,
            "n_evidence_topics": 0,
        }

    return {"overall": overall, "topics": topics_out}
