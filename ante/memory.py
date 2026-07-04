# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Memory model: calibration of recall probabilities.

Anki's FSRS already estimates the probability a card is recalled. Our job here is
not to replace it but to *check it honestly*: when the model says 80%, do students
actually recall ~80% of the time? We report Brier score, log loss, a reliability
diagram (the calibration chart), and expected calibration error on held-out
reviews. Every aggregate recall number ships with a Wilson confidence interval so
the UI can show a range, never a bare point.

Pure Python, no third-party dependencies, so it can run anywhere (including with
AI switched off).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

EPS = 1e-12


def _clip(p: float) -> float:
    return min(1.0 - EPS, max(EPS, p))


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean squared error between predicted prob and {0,1} outcome. Lower better."""
    _check(probs, outcomes)
    n = len(probs)
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n


def log_loss(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean negative log-likelihood. Lower is better."""
    _check(probs, outcomes)
    n = len(probs)
    total = 0.0
    for p, o in zip(probs, outcomes):
        p = _clip(p)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / n


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    count: int
    mean_pred: float
    frac_correct: float


@dataclass(frozen=True)
class CalibrationReport:
    n: int
    brier: float
    log_loss: float
    ece: float  # expected calibration error
    bins: list[Bin]
    observed_recall: float
    recall_ci: tuple[float, float]

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "brier": self.brier,
            "log_loss": self.log_loss,
            "ece": self.ece,
            "observed_recall": self.observed_recall,
            "recall_ci": list(self.recall_ci),
            "bins": [
                {
                    "lo": b.lo,
                    "hi": b.hi,
                    "count": b.count,
                    "mean_pred": b.mean_pred,
                    "frac_correct": b.frac_correct,
                }
                for b in self.bins
            ],
        }


def reliability_bins(
    probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10
) -> list[Bin]:
    _check(probs, outcomes)
    edges = [i / n_bins for i in range(n_bins + 1)]
    bins: list[Bin] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        # last bin is inclusive of 1.0
        sel = [
            (p, o)
            for p, o in zip(probs, outcomes)
            if (p >= lo and p < hi) or (i == n_bins - 1 and p == hi)
        ]
        count = len(sel)
        if count:
            mean_pred = sum(p for p, _ in sel) / count
            frac = sum(o for _, o in sel) / count
        else:
            mean_pred = (lo + hi) / 2
            frac = 0.0
        bins.append(Bin(lo, hi, count, mean_pred, frac))
    return bins


def expected_calibration_error(bins: list[Bin]) -> float:
    n = sum(b.count for b in bins)
    if not n:
        return 0.0
    return sum(b.count * abs(b.mean_pred - b.frac_correct) for b in bins) / n


def wilson_interval(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    ``successes`` may be fractional when pooling partial-credit evidence (e.g.
    open-ended scores in 0..1); the interval then treats the pooled score as an
    effective success count, which is an honest widening approximation."""
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def calibrate(
    probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10
) -> CalibrationReport:
    _check(probs, outcomes)
    n = len(probs)
    bins = reliability_bins(probs, outcomes, n_bins)
    successes = sum(outcomes)
    return CalibrationReport(
        n=n,
        brier=brier_score(probs, outcomes),
        log_loss=log_loss(probs, outcomes),
        ece=expected_calibration_error(bins),
        bins=bins,
        observed_recall=successes / n,
        recall_ci=wilson_interval(successes, n),
    )


def render_reliability_svg(report: CalibrationReport, size: int = 320) -> str:
    """Render the reliability diagram as a dependency-free SVG string."""
    pad = 30
    plot = size - 2 * pad

    def x(v: float) -> float:
        return pad + v * plot

    def y(v: float) -> float:
        return size - pad - v * plot

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" font-family="sans-serif" font-size="10">',
        f'<rect x="0" y="0" width="{size}" height="{size}" fill="white"/>',
        # axes
        f'<line x1="{pad}" y1="{size - pad}" x2="{size - pad}" y2="{size - pad}" stroke="#888"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{size - pad}" stroke="#888"/>',
        # perfect-calibration diagonal
        f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(1)}" y2="{y(1)}" '
        f'stroke="#bbb" stroke-dasharray="4 4"/>',
        f'<text x="{pad}" y="{pad - 10}">Reliability (predicted vs observed recall)</text>',
    ]
    pts = [(b.mean_pred, b.frac_correct) for b in report.bins if b.count > 0]
    path = " ".join(
        f"{'M' if i == 0 else 'L'} {x(px):.1f} {y(py):.1f}"
        for i, (px, py) in enumerate(pts)
    )
    if path:
        parts.append(
            f'<path d="{path}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        )
    for b in report.bins:
        if b.count:
            parts.append(
                f'<circle cx="{x(b.mean_pred):.1f}" cy="{y(b.frac_correct):.1f}" '
                f'r="3" fill="#2563eb"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _check(probs: Sequence[float], outcomes: Sequence[int]) -> None:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have equal length")
    if not probs:
        raise ValueError("need at least one observation")
