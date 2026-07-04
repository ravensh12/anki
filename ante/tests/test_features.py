# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the learning-science features (PRD Section 9)."""

from ante.config import CONFIG
from ante.intentions import (
    default_intentions,
    next_due_intention,
    notification_text,
)
from ante.rewards import (
    BANNED_REWARDS,
    consistency_status,
    mastery_momentum,
    reward_is_allowed,
)
from ante.sessions import daily_plan, plan_micro_session

# ----- micro-sessions -------------------------------------------------------


def test_micro_session_defaults_to_ten_minutes():
    s = plan_micro_session(due_count=500)
    assert s.minutes == CONFIG.default_session_minutes == 10
    # 10 min @ 8s/card = 75 cards
    assert s.target_cards == 75
    assert not s.clears_due


def test_micro_session_never_exceeds_due():
    s = plan_micro_session(due_count=10, minutes=10)
    assert s.target_cards == 10
    assert s.clears_due


def test_daily_plan_splits_budget_into_slots():
    plan = daily_plan(due_count=200, budget_minutes=75)
    assert plan["covers_due_load"]
    assert sum(s["cards"] for s in plan["slots"]) == 200
    assert {s["slot"] for s in plan["slots"]} == {"morning", "during the day", "night"}


# ----- implementation intentions --------------------------------------------


def test_default_intentions_are_cue_anchored_ten_minute_plans():
    ii = default_intentions()
    assert len(ii) == 2
    assert all(i.session_minutes == 10 for i in ii)
    assert any("coffee" in i.cue_text for i in ii)
    # if-then framing (Gollwitzer)
    assert ii[0].if_then.startswith("If it's")


def test_next_due_intention_picks_upcoming_cue():
    ii = default_intentions()  # morning coffee (7), night (21)
    # at 6am, next is morning coffee
    nxt = next_due_intention(ii, current_hour=6)
    assert "coffee" in nxt.cue_text
    # at 8am (after coffee), next is night
    nxt = next_due_intention(ii, current_hour=8)
    assert nxt.cue_text == "night"
    # text is non-guilt
    assert "broke" not in notification_text(nxt).lower()


# ----- rewards policy (Principle 4) ----------------------------------------------


def test_pure_attendance_rewards_banned():
    # PRD 9.5: pure login/attendance mechanics stay banned...
    for banned in ["login_streak", "daily_login_bonus", "leaderboard_rank"]:
        assert banned in BANNED_REWARDS
        assert not reward_is_allowed(banned)
    # ...but the EFFORT-GATED consistency streak is allowed (with mitigations).
    assert reward_is_allowed("consistency_streak")


def test_only_mastery_rewards_allowed():
    assert reward_is_allowed("mastery_momentum")
    assert reward_is_allowed("topic_locked_in")
    assert not reward_is_allowed("daily_login_bonus")


def test_mastery_momentum_moves_only_on_mastery():
    assert mastery_momentum(0).topics_locked_in == 0
    assert "logins" in mastery_momentum(0).message.lower()
    assert "3 topics" in mastery_momentum(3).message


def test_consistency_is_forgiving_never_punishing():
    low = consistency_status(active_days=1, window_days=7)
    assert not low.on_track
    # never frames as a broken streak
    assert "broke" not in low.message.lower()
    assert "no streak" in low.message.lower()
    high = consistency_status(active_days=5, window_days=7)
    assert high.on_track
