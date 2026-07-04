# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Diagnosis: a cross-topic autopsy of what's really holding you back.

The mastery map shows *state*; this names the single most important *pattern*.
It stitches together the signals the other models already produce \u2014 the
memory\u2192application gap, the prerequisite graph, confidence calibration, coverage
blind spots, and the corrective backlog \u2014 and ranks them into a short, blunt list
of what to fix first. Pure logic; unit-tested without Anki.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .coverage import CoverageReport
from .mastery import MasteryStatus, TopicMastery


@dataclass(frozen=True)
class Insight:
    kind: str
    severity: float  # 0..1
    headline: str
    detail: str
    action: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": round(self.severity, 3),
            "headline": self.headline,
            "detail": self.detail,
            "action": self.action,
        }


def _nice_section(sid: str) -> str:
    return {
        "bio_biochem": "Bio/Biochem",
        "chem_phys": "Chem/Phys",
        "psych_soc": "Psych/Soc",
        "cars": "CARS",
    }.get(sid, sid)


def diagnose(
    mastery: Mapping[str, TopicMastery],
    gaps: list[dict],
    calibration: dict | None = None,
    coverage: CoverageReport | None = None,
    rhythm: dict | None = None,
) -> dict:
    insights: list[Insight] = []

    # 1) transfer bottleneck: recall outruns application on multiple topics
    big_gaps = [g for g in (gaps or []) if g.get("gap", 0) >= 0.2]
    if len(big_gaps) >= 2:
        avg = sum(g["gap"] for g in big_gaps) / len(big_gaps)
        insights.append(
            Insight(
                kind="transfer_gap",
                severity=min(1.0, 0.45 + len(big_gaps) * 0.08 + avg * 0.4),
                headline="Your memory is outrunning your application.",
                detail=(
                    f"On {len(big_gaps)} topics you recall the fact but miss the "
                    f"transfer question (avg gap {avg:.0%}). That's the exact trap the "
                    "MCAT sets \u2014 it tests reasoning, not recall."
                ),
                action="Hit the Quiz on those topics and study for the WHY, not the wording.",
            )
        )

    # 2) keystone prereq: one unmastered topic blocks several others
    blockers: dict[str, int] = {}
    for m in mastery.values():
        if m.status == MasteryStatus.LOCKED:
            for p in m.blocked_by:
                blockers[p] = blockers.get(p, 0) + 1
    if blockers:
        keystone, blocked_n = max(blockers.items(), key=lambda kv: kv[1])
        if blocked_n >= 2:
            km = mastery.get(keystone)
            name = km.name if km else keystone
            insights.append(
                Insight(
                    kind="keystone",
                    severity=min(1.0, 0.5 + blocked_n * 0.1),
                    headline=f"One topic is gating {blocked_n} others.",
                    detail=(
                        f"'{name}' is an unmastered prerequisite blocking {blocked_n} "
                        "downstream topics. Clearing it unlocks the most map at once."
                    ),
                    action=f"Prioritize mastering {name} next.",
                )
            )

    # 3) overconfidence (the dangerous calibration direction)
    if calibration and calibration.get("available"):
        bias = calibration.get("bias", 0.0)
        if bias > 0.1:
            worst = calibration.get("worst_section")
            where = f" (worst in {_nice_section(worst)})" if worst else ""
            insights.append(
                Insight(
                    kind="overconfidence",
                    severity=min(1.0, 0.4 + bias),
                    headline="You think you know it better than you do.",
                    detail=(
                        f"Your confidence runs about {bias:.0%} ahead of your accuracy"
                        f"{where}. Overconfidence is what turns 'I studied that' into a "
                        "missed question."
                    ),
                    action="Slow down on items you feel sure about; verify before moving on.",
                )
            )

    # 4) coverage blind spot on a high-weight section
    if coverage and coverage.missing_high_weight_sections:
        sids = ", ".join(
            _nice_section(s) for s in coverage.missing_high_weight_sections
        )
        insights.append(
            Insight(
                kind="blind_spot",
                severity=0.9,
                headline=f"Blind spot: {sids} is barely covered.",
                detail=(
                    "A high-weight section is nearly empty, so any readiness number "
                    "would be dishonest. This caps your projected score."
                ),
                action=f"Add or import cards for {sids}.",
            )
        )

    # 5) corrective backlog
    corrective = [m for m in mastery.values() if m.status == MasteryStatus.CORRECTIVE]
    if len(corrective) >= 3:
        insights.append(
            Insight(
                kind="corrective_backlog",
                severity=min(1.0, 0.35 + len(corrective) * 0.05),
                headline=f"{len(corrective)} topics have slipped to corrective.",
                detail=(
                    "These were on track but fell below the application bar. They "
                    "compound if left \u2014 corrective topics are your fastest wins back."
                ),
                action="Clear the corrective queue before opening new topics.",
            )
        )

    # 6) peak-hours nudge (only when it's a real edge)
    if rhythm and rhythm.get("available") and rhythm.get("advice"):
        best = rhythm.get("best_window")
        insights.append(
            Insight(
                kind="peak_hours",
                severity=0.3,
                headline=f"Your sharpest window is the {best}.",
                detail=rhythm["advice"],
                action=f"Schedule your hardest section in the {best}.",
            )
        )

    insights.sort(key=lambda i: i.severity, reverse=True)
    return {
        "available": bool(insights),
        "insights": [i.as_dict() for i in insights],
        "top": insights[0].as_dict() if insights else None,
    }
