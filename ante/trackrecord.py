# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Readiness track record — "how accurate your past guesses turned out to be".

The spec's honesty rule (section 1) says a readiness number may only be shown
alongside *how accurate the past guesses were*. This module keeps a log of the
lines the Book has posted over time and, whenever the student completes a
full-length practice test (a real observed score), pairs each earlier posted
line with the next actual score and reports:

  * how many posted lines have since been checked against a real score,
  * how often the actual score landed inside the posted range (calibration of
    the interval), and
  * the mean absolute error of the point estimate (in MCAT points).

Below a minimum number of checks it abstains — the same "know when you don't
know" rule as readiness itself, applied to the track record. Pure logic; unit
tested without Anki.
"""

from __future__ import annotations

from dataclasses import dataclass

SECONDS_PER_DAY = 86400.0
# a posted line is only meaningfully checkable against a practice test taken
# within this window afterwards (older lines describe a different student)
DEFAULT_HORIZON_DAYS = 45.0
# need at least this many checked lines before we report a hit rate
MIN_CHECKS = 1


@dataclass(frozen=True)
class Check:
    posted_at: float
    projected_total: int
    low: int
    high: int
    actual_at: float
    actual_total: int

    @property
    def within_range(self) -> bool:
        return self.low <= self.actual_total <= self.high

    @property
    def abs_error(self) -> int:
        return abs(self.projected_total - self.actual_total)

    def as_dict(self) -> dict:
        return {
            "posted_at": self.posted_at,
            "projected_total": self.projected_total,
            "range": [self.low, self.high],
            "actual_at": self.actual_at,
            "actual_total": self.actual_total,
            "within_range": self.within_range,
            "abs_error": self.abs_error,
        }


@dataclass(frozen=True)
class TrackRecord:
    n_checks: int
    n_within_range: int
    mean_abs_error: float | None
    checks: tuple[Check, ...]
    min_checks: int = MIN_CHECKS

    @property
    def abstained(self) -> bool:
        return self.n_checks < self.min_checks

    @property
    def hit_rate(self) -> float | None:
        if self.n_checks == 0:
            return None
        return self.n_within_range / self.n_checks

    def as_dict(self) -> dict:
        return {
            "abstained": self.abstained,
            "n_checks": self.n_checks,
            "n_within_range": self.n_within_range,
            "hit_rate": self.hit_rate,
            "mean_abs_error": (
                round(self.mean_abs_error, 1)
                if self.mean_abs_error is not None
                else None
            ),
            "checks": [c.as_dict() for c in self.checks],
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if self.abstained:
            return (
                "Not enough completed practice tests yet to score past lines — "
                "the Book abstains on its own track record too."
            )
        pct = round(100 * (self.hit_rate or 0))
        return (
            f"{self.n_within_range}/{self.n_checks} past lines contained the "
            f"actual score ({pct}%); mean miss {self.mean_abs_error:.0f} points."
        )


def append_line(
    history: list[dict],
    line: dict,
    now: float,
    min_gap_days: float = 0.8,
) -> list[dict]:
    """Append a posted readiness line to the history, at most once per ~day.

    Only non-abstaining lines with a numeric projected total are recorded. If
    the most recent entry is within ``min_gap_days`` it is replaced (we keep the
    latest read per day) so the log doesn't bloat with every dashboard refresh.
    """
    if line.get("abstained") or line.get("projected_total") is None:
        return history
    rng = line.get("total_range") or [None, None]
    if rng[0] is None or rng[1] is None:
        return history
    entry = {
        "ts": float(now),
        "projected_total": int(line["projected_total"]),
        "low": int(rng[0]),
        "high": int(rng[1]),
        "confidence": line.get("confidence"),
    }
    hist = list(history or [])
    if hist and (now - float(hist[-1].get("ts", 0))) < min_gap_days * SECONDS_PER_DAY:
        # same "day": if the line hasn't moved, keep the earlier entry verbatim
        # (honest first-posted time; avoids a write on every dashboard load).
        last = hist[-1]
        if all(last.get(k) == entry[k] for k in ("projected_total", "low", "high")):
            return history
        hist[-1] = entry
    else:
        hist.append(entry)
    return hist


def _actual_scores(fl_results: dict) -> list[tuple[float, int]]:
    """(taken_at, total) pairs from stored full-length results, chronological."""
    out: list[tuple[float, int]] = []
    for res in (fl_results or {}).values():
        if not isinstance(res, dict):
            continue
        total = res.get("total")
        taken = res.get("taken_at")
        if total is None or taken is None:
            continue
        out.append((float(taken), int(total)))
    out.sort()
    return out


def evaluate(
    history: list[dict],
    fl_results: dict,
    horizon_days: float = DEFAULT_HORIZON_DAYS,
    min_checks: int = MIN_CHECKS,
) -> TrackRecord:
    """Pair each posted line with the FIRST full-length taken after it (within
    the horizon) and score the prediction against that actual total."""
    actuals = _actual_scores(fl_results)
    checks: list[Check] = []
    for entry in history or []:
        posted_at = float(entry.get("ts", 0))
        nxt = next(
            (
                (t, total)
                for t, total in actuals
                if t > posted_at and (t - posted_at) <= horizon_days * SECONDS_PER_DAY
            ),
            None,
        )
        if nxt is None:
            continue
        actual_at, actual_total = nxt
        checks.append(
            Check(
                posted_at=posted_at,
                projected_total=int(entry["projected_total"]),
                low=int(entry["low"]),
                high=int(entry["high"]),
                actual_at=actual_at,
                actual_total=actual_total,
            )
        )
    n = len(checks)
    within = sum(1 for c in checks if c.within_range)
    mae = sum(c.abs_error for c in checks) / n if n else None
    return TrackRecord(
        n_checks=n,
        n_within_range=within,
        mean_abs_error=mae,
        checks=tuple(checks),
        min_checks=min_checks,
    )
