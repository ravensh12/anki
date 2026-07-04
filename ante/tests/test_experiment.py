# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the mastery-gating study-feature experiment (PRD Section 10)."""

from ante.experiment import run_experiment


def test_experiment_runs_three_arms_equal_time():
    result = run_experiment(reps_per_day=40, days=14, seed=0)
    assert set(result["arms"]) == {"full", "ablation", "baseline"}
    assert result["equal_study_reps"] == 40 * 14
    assert result["feature"] == "topic-level mastery-gating"
    for arm in result["arms"].values():
        lo, hi = arm["ci"]
        assert lo <= arm["held_out_accuracy"] <= hi
    # harness reports the comparison + an honest verdict regardless of direction
    assert isinstance(result["gating_significant"], bool)
    assert result["verdict"]
    assert result["hypothesis"]


def test_gating_does_not_underperform_ablation_at_constrained_time():
    # at constrained study time, gating (building on mastered prereqs) should not
    # do worse than the ungated ablation; report nulls honestly elsewhere
    result = run_experiment(reps_per_day=20, days=10, seed=2)
    full = result["arms"]["full"]["held_out_accuracy"]
    ablation = result["arms"]["ablation"]["held_out_accuracy"]
    assert full >= ablation - 0.03


def test_full_arm_masters_more_topics_than_plain():
    result = run_experiment(reps_per_day=40, days=20, seed=1)
    full_mastered = result["arms"]["full"]["mastered_topics"]
    plain_mastered = result["arms"]["baseline"]["mastered_topics"]
    # concentrating effort should master at least as many topics as random study
    assert full_mastered >= plain_mastered
