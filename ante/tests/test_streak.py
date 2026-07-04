# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the effort-gated consistency streak + reward (PRD 9.5)."""

from dataclasses import replace

from ante.config import CONFIG
from ante.rewards import (
    compute_streak,
    day_counts,
    mastery_milestone_reward,
    reward_is_allowed,
)


def test_effort_gate_not_attendance():
    # opening the app / tapping a couple cards does NOT count
    assert not day_counts(0)
    assert not day_counts(CONFIG.streak_min_reviews - 1)
    # a real session counts
    assert day_counts(CONFIG.streak_min_reviews)
    assert day_counts(CONFIG.streak_min_reviews + 50)


def test_streak_counts_consecutive_effort_days():
    today = 1000
    genuine = {today - i: 30 for i in range(5)}  # 5 straight real days incl today
    s = compute_streak(genuine, today)
    assert s.current_streak == 5
    assert s.today_counts
    assert s.reward_estimate <= CONFIG.reward_cap


def test_freeze_absorbs_a_rest_day():
    today = 1000
    # days: today ok, yesterday MISSED, then 3 more ok -> freeze should bridge it
    genuine = {today: 30, today - 2: 30, today - 3: 30, today - 4: 30}
    s = compute_streak(genuine, today)
    # streak spans the gap via one freeze
    assert s.current_streak >= 4
    assert s.freezes_remaining == CONFIG.streak_freezes_per_month - 1


def test_streak_breaks_when_freezes_exhausted():
    cfg = replace(CONFIG, streak_freezes_per_month=0)
    today = 1000
    genuine = {today: 30, today - 2: 30}  # gap at today-1, no freezes
    s = compute_streak(genuine, today, cfg)
    assert s.current_streak == 1  # only today


def test_no_shame_framing():
    s = compute_streak({}, 1000)
    assert s.current_streak == 0
    # never punitive
    assert "broke" not in s.message.lower()
    assert "opening the app" in s.message.lower()


def test_reward_is_capped_and_effort_gated():
    today = 1000
    genuine = {today - i: 60 for i in range(40)}  # very long, high effort
    s = compute_streak(genuine, today)
    assert s.reward_estimate <= CONFIG.reward_cap  # cap-and-sunset holds
    assert s.multiplier <= 2.0


def test_gift_card_lands_at_thirty_honest_nights():
    today = 1000
    # 29 nights: still climbing, gift card not yet earned
    s29 = compute_streak({today - i: 30 for i in range(29)}, today)
    assert s29.target_days == 30
    assert not s29.gift_card_earned
    assert "1 to the gift card" in s29.message
    # 30 nights: earned
    s30 = compute_streak({today - i: 30 for i in range(30)}, today)
    assert s30.gift_card_earned
    assert "gift card is yours" in s30.message
    # and it is effort-gated: 30 days of app-opens (too few reviews) earn nothing
    s0 = compute_streak({today - i: 2 for i in range(30)}, today)
    assert s0.current_streak == 0 and not s0.gift_card_earned


def test_policy_allows_effort_streak_bans_login_streak():
    assert reward_is_allowed("consistency_streak")
    assert reward_is_allowed("mastery_momentum")
    assert not reward_is_allowed("login_streak")
    assert not reward_is_allowed("daily_login_bonus")


def test_milestone_reward_fires_on_mastery():
    assert "real learning" in mastery_milestone_reward(3).message
    assert mastery_milestone_reward(0).topics_mastered == 0


def test_streak_experiment_measures_overjustification():
    from ante.experiment import run_streak_experiment

    # awaiting telemetry: returns the design + decision rule, no fake numbers
    design = run_streak_experiment()
    assert design["status"] == "awaiting telemetry"
    assert "overjustification" in " ".join(design["primary_metrics"]).lower()

    # a post-reward collapse must be flagged harmful
    res = run_streak_experiment(
        during={
            "reward": {"held_out_accuracy": 0.71, "return_rate": 0.9},
            "control": {"held_out_accuracy": 0.70, "return_rate": 0.8},
        },
        after={
            "reward": {"held_out_accuracy": 0.70, "return_rate": 0.55},
            "control": {"held_out_accuracy": 0.70, "return_rate": 0.75},
        },
    )
    assert res["harmful"] is True
