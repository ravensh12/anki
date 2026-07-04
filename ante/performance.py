# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Performance model: the memory -> performance bridge.

Remembering a flashcard is not the same as answering a new exam-style question
that *uses* the fact. This module predicts the probability a student answers a
novel question correctly from features that go beyond raw recall: topic mastery,
question difficulty, response timing, and coverage. The whole point is to expose
the gap between memory and application; if our model just copies the memory
signal, we have not built the bridge.

We therefore always compare against a "memory = performance" baseline and report
the paraphrase-test gap. Pure-Python logistic regression (no sklearn) so it runs
with AI off.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

FEATURES = ["topic_mastery", "difficulty", "response_time_z", "coverage"]


def sigmoid(z: float) -> float:
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


@dataclass
class LogisticRegression:
    lr: float = 0.1
    epochs: int = 500
    l2: float = 1e-4
    weights: list[float] = None  # type: ignore
    bias: float = 0.0
    mean_: list[float] = None  # type: ignore
    std_: list[float] = None  # type: ignore

    def _standardize(self, X: Sequence[Sequence[float]]) -> list[list[float]]:
        return [
            [(x[j] - self.mean_[j]) / self.std_[j] for j in range(len(self.mean_))]
            for x in X
        ]

    def fit(
        self, X: Sequence[Sequence[float]], y: Sequence[int]
    ) -> "LogisticRegression":
        n = len(X)
        d = len(X[0])
        # feature standardization for stable training
        self.mean_ = [sum(x[j] for x in X) / n for j in range(d)]
        self.std_ = [
            math.sqrt(sum((x[j] - self.mean_[j]) ** 2 for x in X) / n) or 1.0
            for j in range(d)
        ]
        Xs = self._standardize(X)
        self.weights = [0.0] * d
        self.bias = 0.0
        for _ in range(self.epochs):
            gw = [0.0] * d
            gb = 0.0
            for xi, yi in zip(Xs, y):
                pred = sigmoid(sum(w * x for w, x in zip(self.weights, xi)) + self.bias)
                err = pred - yi
                for j in range(d):
                    gw[j] += err * xi[j]
                gb += err
            for j in range(d):
                gw[j] = gw[j] / n + self.l2 * self.weights[j]
                self.weights[j] -= self.lr * gw[j]
            self.bias -= self.lr * (gb / n)
        return self

    def predict_proba(self, X: Sequence[Sequence[float]]) -> list[float]:
        Xs = self._standardize(X)
        return [
            sigmoid(sum(w * x for w, x in zip(self.weights, xi)) + self.bias)
            for xi in Xs
        ]


@dataclass(frozen=True)
class EvalResult:
    accuracy: float
    log_loss: float
    wrong_rate: float

    def as_dict(self) -> dict:
        return {
            "accuracy": self.accuracy,
            "log_loss": self.log_loss,
            "wrong_rate": self.wrong_rate,
        }


def evaluate(probs: Sequence[float], outcomes: Sequence[int]) -> EvalResult:
    from .memory import log_loss as _ll

    n = len(outcomes)
    preds = [1 if p >= 0.5 else 0 for p in probs]
    correct = sum(1 for pr, o in zip(preds, outcomes) if pr == o)
    return EvalResult(
        accuracy=correct / n,
        log_loss=_ll(probs, outcomes),
        wrong_rate=(n - correct) / n,
    )


def memory_baseline_probs(X: Sequence[Sequence[float]]) -> list[float]:
    """Baseline that equates performance with memory: predict the topic_mastery
    feature directly. If the trained model can't beat this, the bridge is fake."""
    idx = FEATURES.index("topic_mastery")
    return [min(1.0, max(0.0, x[idx])) for x in X]


@dataclass(frozen=True)
class ParaphraseGap:
    n: int
    mean_card_recall: float
    mean_reworded_accuracy: float
    gap: float

    @property
    def meaningful(self) -> bool:
        # If recall and application track each other almost exactly, the
        # "performance" signal is just memory in disguise.
        return self.gap > 0.05

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean_card_recall": self.mean_card_recall,
            "mean_reworded_accuracy": self.mean_reworded_accuracy,
            "gap": self.gap,
            "meaningful": self.meaningful,
        }


def paraphrase_gap(
    card_recall: Sequence[float], reworded_accuracy: Sequence[float]
) -> ParaphraseGap:
    """The spec's paraphrase test: for each card, compare recall on the card with
    accuracy on reworded questions testing the same idea. Report the gap."""
    if len(card_recall) != len(reworded_accuracy) or not card_recall:
        raise ValueError("need equal, non-empty sequences")
    n = len(card_recall)
    mr = sum(card_recall) / n
    mw = sum(reworded_accuracy) / n
    return ParaphraseGap(
        n=n, mean_card_recall=mr, mean_reworded_accuracy=mw, gap=mr - mw
    )


def bootstrap_mean_ci(
    values: Sequence[float], iters: int = 1000, seed: int = 0
) -> tuple[float, float, float]:
    """Point estimate + 95% bootstrap CI for the mean of a sequence."""
    if not values:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    point = sum(values) / n
    means = []
    for _ in range(iters):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[min(iters - 1, int(0.975 * iters))]
    return (point, lo, hi)


def section_accuracy_estimates(
    section_to_probs: dict[str, Sequence[float]], seed: int = 0
) -> dict[str, tuple[float, float, float]]:
    """Aggregate predicted per-question performance into per-section
    (point, low, high) accuracy estimates for the readiness model."""
    return {
        sid: bootstrap_mean_ci(probs, seed=seed)
        for sid, probs in section_to_probs.items()
        if probs
    }
