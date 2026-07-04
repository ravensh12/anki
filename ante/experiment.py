# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Study-feature experiment: topic-level mastery-gating (PRD Section 10).

The chosen feature is the product's differentiator: gating progression on
demonstrated topic mastery. Three arms at EQUAL study time (PRD 10.3):

  1. full      - mastery-gating ON: only study unlocked topics (prereqs mastered),
                 weakest-first, and don't advance until a topic is mastered.
  2. ablation  - same app, gating OFF: spaced cards across all topics, weakest
                 first, but no unlock graph (may study topics whose prereqs are weak).
  3. baseline  - plain Anki: uniform random study, no topic awareness at all.

PRE-REGISTERED HYPOTHESIS (PRD 10.2):
  "Gating progression on demonstrated topic mastery raises accuracy on held-out
   exam-style questions at equal study time, versus the same spaced cards without
   gating."
PRIMARY METRIC: held-out exam-style accuracy on covered topics at a fixed delay,
equal study minutes across arms.

This is a simulation harness (no human cohort in a week). Its learner model
encodes one mechanism from the evidence: learning a topic whose prerequisites are
NOT mastered is less durable (Bloom: building on un-mastered prerequisites is
building on sand). Real review logs can replace the learner model later. We report
a range and report nulls honestly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import CONFIG
from .outline import load_outline
from .performance import bootstrap_mean_ci

HYPOTHESIS = (
    "Gating progression on demonstrated topic mastery raises accuracy on held-out "
    "exam-style questions at equal study time, versus the same spaced cards "
    "without gating."
)
PRIMARY_METRIC = "held-out exam-style accuracy on covered topics at equal study minutes"

# How much prerequisite mastery helps durable learning of a dependent topic.
# 1.0 => prereqs irrelevant; lower => learning on shaky prereqs is less durable.
PREREQ_PENALTY = 0.45


@dataclass
class _Topic:
    tag: str
    weight: float
    prereqs: tuple[str, ...]
    mastery: float = 0.0


def _topics_from_outline(rng: random.Random) -> dict[str, _Topic]:
    outline = load_outline()
    topics: dict[str, _Topic] = {}
    for t in outline.all_topic_objs():
        topics[t.tag] = _Topic(
            tag=t.tag,
            weight=outline.topic_weight(t.tag) * (0.5 + t.exam_weight),
            prereqs=t.prereqs,
        )
    return topics


def _prereq_readiness(t: _Topic, topics: dict[str, _Topic], bar: float) -> float:
    """1.0 if all prereqs mastered; otherwise scaled down (Bloom)."""
    if not t.prereqs:
        return 1.0
    ready = sum(1 for p in t.prereqs if topics.get(p, _Topic(p, 1, ())).mastery >= bar)
    frac = ready / len(t.prereqs)
    return PREREQ_PENALTY + (1.0 - PREREQ_PENALTY) * frac


def _study_once(t: _Topic, topics: dict[str, _Topic], bar: float) -> None:
    readiness = _prereq_readiness(t, topics, bar)
    gain = (1.0 - t.mastery) * 0.30 * readiness
    t.mastery = min(1.0, t.mastery + gain)


def _unlocked(t: _Topic, topics: dict[str, _Topic], bar: float) -> bool:
    return all(topics.get(p, _Topic(p, 1, ())).mastery >= bar for p in t.prereqs)


def _allocate(
    arm: str, topics: dict[str, _Topic], reps: int, rng: random.Random, bar: float
) -> None:
    vals = list(topics.values())
    for _ in range(reps):
        if arm == "full":
            # gating ON: only unlocked topics; among those, weakest not-yet-mastered
            pool = [t for t in vals if _unlocked(t, topics, bar) and t.mastery < bar]
            if not pool:
                pool = [t for t in vals if _unlocked(t, topics, bar)] or vals
            target = max(pool, key=lambda t: t.weight * (1.0 - t.mastery))
        elif arm == "ablation":
            # gating OFF: weakest-first across ALL topics (ignores prereqs)
            target = max(vals, key=lambda t: t.weight * (1.0 - t.mastery))
        else:  # baseline: plain Anki, uniform random
            target = vals[rng.randrange(len(vals))]
        _study_once(target, topics, bar)


def _evaluate(topics: dict[str, _Topic], rng: random.Random, n_items: int = 800):
    vals = list(topics.values())
    total_w = sum(t.weight for t in vals)
    flags: list[float] = []
    for _ in range(n_items):
        r = rng.uniform(0, total_w)
        acc = 0.0
        chosen = vals[-1]
        for t in vals:
            acc += t.weight
            if r <= acc:
                chosen = t
                break
        flags.append(1.0 if rng.random() < chosen.mastery else 0.0)
    return flags


@dataclass(frozen=True)
class ArmResult:
    arm: str
    accuracy: float
    ci: tuple[float, float]
    mastered_topics: int = 0

    def as_dict(self) -> dict:
        return {
            "arm": self.arm,
            "held_out_accuracy": round(self.accuracy, 4),
            "ci": [round(x, 4) for x in self.ci],
            "mastered_topics": self.mastered_topics,
        }


def _run_arm(arm: str, reps: int, seed: int, bar: float) -> ArmResult:
    rng = random.Random(seed)
    topics = _topics_from_outline(rng)
    _allocate(arm, topics, reps, rng, bar)
    flags = _evaluate(topics, rng)
    point, lo, hi = bootstrap_mean_ci(flags, seed=seed)
    mastered = sum(1 for t in topics.values() if t.mastery >= bar)
    return ArmResult(arm, point, (lo, hi), mastered)


def run_experiment(reps_per_day: int = 40, days: int = 14, seed: int = 0) -> dict:
    reps = reps_per_day * days
    bar = CONFIG.mastery_bar
    results = {
        arm: _run_arm(arm, reps, seed, bar) for arm in ("full", "ablation", "baseline")
    }
    full, ablation, baseline = results["full"], results["ablation"], results["baseline"]
    delta_gating = full.accuracy - ablation.accuracy  # isolates the feature
    delta_vs_plain = full.accuracy - baseline.accuracy
    # significant if full's CI lower bound exceeds the ablation point estimate
    significant = full.ci[0] > ablation.accuracy

    return {
        "feature": "topic-level mastery-gating",
        "hypothesis": HYPOTHESIS,
        "primary_metric": PRIMARY_METRIC,
        "equal_study_reps": reps,
        "mastery_bar": bar,
        "arms": {k: v.as_dict() for k, v in results.items()},
        "delta_full_vs_ablation": round(delta_gating, 4),
        "delta_full_vs_plain": round(delta_vs_plain, 4),
        "gating_significant": significant,
        "verdict": _verdict(delta_gating, delta_vs_plain, significant),
    }


def _verdict(delta_gating: float, delta_vs_plain: float, significant: bool) -> str:
    if significant and delta_gating > 0:
        return (
            f"Mastery-gating raised held-out accuracy by {delta_gating:+.1%} vs the "
            f"ungated ablation (and {delta_vs_plain:+.1%} vs plain Anki) at equal "
            f"study time."
        )
    return (
        f"No significant gating effect here ({delta_gating:+.1%} vs ablation); "
        f"reporting the null honestly. A fair test that could show the feature "
        f"does not help is still a useful result."
    )


STREAK_HYPOTHESIS = (
    "Adding the effort-gated consistency streak + gift-card reward (both groups "
    "keep the mastery-momentum signal) does NOT reduce learning during the reward "
    "period and does NOT cause an engagement collapse after the reward ends."
)


def run_streak_experiment(
    during: dict | None = None,
    after: dict | None = None,
) -> dict:
    """A/B measurement harness for the streak+reward layer (PRD 9.5.5).

    Consumes two cohorts' telemetry — ``reward`` (streak ON) and ``control``
    (streak OFF) — for the reward period (``during``) and the post-reward period
    (``after``), each with keys: ``held_out_accuracy`` and ``return_rate``. The
    critical tell is the *post-reward* comparison (overjustification). Applies the
    decision rule and returns a verdict; refuses to fake data when none is given.
    """
    if not during or not after:
        return {
            "hypothesis": STREAK_HYPOTHESIS,
            "primary_metrics": [
                "held-out performance/retention",
                "30/60-day return rate",
                "retention AFTER the reward period ends (overjustification tell)",
            ],
            "status": "awaiting telemetry",
            "decision_rule": (
                "If the reward group shows equal-or-worse learning, or a "
                "post-reward engagement collapse vs control, cut or restructure "
                "the reward. Instrument it; be willing to kill it."
            ),
        }

    learn_delta = (
        during["reward"]["held_out_accuracy"] - during["control"]["held_out_accuracy"]
    )
    post_return_delta = after["reward"]["return_rate"] - after["control"]["return_rate"]
    harmful = learn_delta < 0 or post_return_delta < -0.05
    verdict = (
        "Cut/restructure the reward: it did not help learning and/or engagement "
        "collapsed after rewards stopped (overjustification)."
        if harmful
        else "Keep (for now): no learning penalty and no post-reward collapse. Keep measuring."
    )
    return {
        "hypothesis": STREAK_HYPOTHESIS,
        "learn_delta_during": round(learn_delta, 4),
        "post_reward_return_delta": round(post_return_delta, 4),
        "harmful": harmful,
        "verdict": verdict,
    }


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Run the mastery-gating experiment.")
    parser.add_argument("--reps-per-day", type=int, default=40)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(run_experiment(args.reps_per_day, args.days, args.seed), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
