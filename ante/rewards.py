# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Motivation & rewards (PRD 9.5) — Principle 4, with a documented, mitigated tradeoff.

Two motivation surfaces:
  1. Mastery-momentum (always on, thesis-aligned): moves ONLY on demonstrated
     mastery; cannot be faked by logins.
  2. A consistency streak + monthly gift-card reward (extrinsic layer, 9.5.2):
     included by product decision but EFFORT-GATED (min genuine reviews +
     plausible response times), FREEZE-forgiving, mastery-paired, no-shame, and
     cap-and-sunset -- because it sits in tension with Principle 4.

Governing test (`reward_is_allowed`): does it fire when learning is real, or just
when attendance is logged? A pure login/attendance streak stays banned; the
consistency streak passes only to the degree its effort-gate holds, which is why
it is measured (9.5.5) and killable. The honest tradeoff is recorded verbatim in
ante/docs/rewards-tradeoff.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CONFIG, AnteConfig

# Pure attendance mechanics remain banned (fire on logins, not learning).
BANNED_REWARDS = frozenset({"login_streak", "daily_login_bonus", "leaderboard_rank"})
# Allowed: mastery signals + the EFFORT-GATED consistency streak/reward.
ALLOWED_REWARDS = frozenset(
    {
        "mastery_momentum",
        "topic_locked_in",
        "on_track",
        "consistency_streak",
        "mastery_milestone_reward",
    }
)


def reward_is_allowed(kind: str) -> bool:
    """Allowed only if it fires on real learning, not attendance. The consistency
    streak qualifies solely because of its effort-gate (PRD 9.5.2)."""
    return kind in ALLOWED_REWARDS and kind not in BANNED_REWARDS


@dataclass(frozen=True)
class MasteryMomentum:
    topics_locked_in: int
    window_days: int
    message: str

    def as_dict(self) -> dict:
        return {
            "topics_locked_in": self.topics_locked_in,
            "window_days": self.window_days,
            "message": self.message,
        }


def mastery_momentum(
    newly_mastered_count: int, window_days: int = 7
) -> MasteryMomentum:
    """The honest competence signal (SDT competence need): topics genuinely
    locked in over a window. Moves only on demonstrated mastery."""
    if newly_mastered_count <= 0:
        msg = "No new topics locked in yet — that's fine. Mastery counts, not logins."
    elif newly_mastered_count == 1:
        msg = f"1 topic locked in over the last {window_days} days."
    else:
        msg = (
            f"{newly_mastered_count} topics locked in over the last {window_days} days."
        )
    return MasteryMomentum(newly_mastered_count, window_days, msg)


@dataclass(frozen=True)
class ConsistencyStatus:
    active_days: int
    window_days: int
    on_track: bool
    message: str

    def as_dict(self) -> dict:
        return {
            "active_days": self.active_days,
            "window_days": self.window_days,
            "on_track": self.on_track,
            "message": self.message,
        }


def consistency_status(
    active_days: int, window_days: int = 7, target_days: int = 4
) -> ConsistencyStatus:
    """Forgiving consistency (PRD 9.5): framed as 'on track', never 'you broke
    your chain'. Rest days are legitimate; we never punish them."""
    on_track = active_days >= target_days
    if on_track:
        msg = f"You're on track — {active_days} of the last {window_days} days studied."
    else:
        msg = (
            f"{active_days} of the last {window_days} days studied. No streak to "
            f"break — pick up the top of the stack whenever you're ready."
        )
    return ConsistencyStatus(active_days, window_days, on_track, msg)


# --------------------------------------------------------------------------- #
# Consistency streak + monthly gift-card reward (PRD 9.5.2 / 9.5.3)
# --------------------------------------------------------------------------- #


def day_counts(genuine_reviews: int, cfg: AnteConfig | None = None) -> bool:
    """Effort-gate (9.5.2): a day counts ONLY when real study happened — at least
    STREAK_MIN_REVIEWS genuinely-attempted reviews (plausible response times).
    Opening the app is never enough."""
    cfg = cfg or CONFIG
    return genuine_reviews >= cfg.streak_min_reviews


@dataclass(frozen=True)
class StreakStatus:
    current_streak: int
    longest_streak: int
    multiplier: float
    today_counts: bool
    freezes_remaining: int
    reward_estimate: float
    message: str
    target_days: int = 30
    gift_card_earned: bool = False

    def as_dict(self) -> dict:
        return {
            "current_streak": self.current_streak,
            "longest_streak": self.longest_streak,
            "multiplier": round(self.multiplier, 2),
            "today_counts": self.today_counts,
            "freezes_remaining": self.freezes_remaining,
            "reward_estimate": round(self.reward_estimate, 2),
            "message": self.message,
            "target_days": self.target_days,
            "gift_card_earned": self.gift_card_earned,
        }


def compute_streak(
    genuine_by_day: dict[int, int],
    today_ordinal: int,
    cfg: AnteConfig | None = None,
    reward_target_days: int = 30,
) -> StreakStatus:
    """Compute the effort-gated consistency streak.

    ``genuine_by_day`` maps a day ordinal -> count of genuine reviews that day.
    Walking back from today, a day extends the streak if it meets the effort-gate;
    a non-qualifying day may be absorbed by a monthly "freeze" (rest-day
    allowance) instead of breaking the streak (9.5.3). Framing is never punitive.
    """
    cfg = cfg or CONFIG
    counts_day = {d: day_counts(n, cfg) for d, n in genuine_by_day.items()}
    counting = {d for d, ok in counts_day.items() if ok}
    today_counts = today_ordinal in counting

    # current streak: consecutive counting days back from the anchor (today if it
    # counts, else yesterday so an open 'today' doesn't break the chain). Internal
    # gaps are absorbed by freezes; we stop at the earliest real day so freezes are
    # never wasted on empty prehistory.
    freezes = cfg.streak_freezes_per_month
    freezes_used = 0
    streak = 0
    if counting:
        min_day = min(counting)
        day = today_ordinal if today_counts else today_ordinal - 1
        while day >= min_day:
            if day in counting:
                streak += 1
            elif freezes_used < freezes:
                freezes_used += 1
            else:
                break
            day -= 1

    # longest streak over the record (simple scan, freeze-free)
    longest = 0
    run = 0
    for day in range(min(genuine_by_day, default=today_ordinal), today_ordinal + 1):
        if counts_day.get(day, False):
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    # multiplier grows with sustained consistency, resets conceptually monthly, capped
    multiplier = min(2.0, 1.0 + 0.25 * (streak // 7))
    # modest, capped monthly reward; scales with consistent days toward a target
    consistent_this_month = sum(
        1 for day, ok in counts_day.items() if ok and today_ordinal - day < 30
    )
    reward = min(
        cfg.reward_cap,
        cfg.reward_cap
        * min(1.0, consistent_this_month / reward_target_days)
        * multiplier,
    )

    gift_card_earned = streak >= reward_target_days
    if streak == 0:
        msg = "No run yet — that's fine. A real session counts; opening the app doesn't."
    elif gift_card_earned:
        msg = (
            f"{streak}-night run — the {reward_target_days}-night gift card is "
            "yours. Earned on real play, never on app-opens."
        )
    else:
        msg = (
            f"{streak}-night run (effort-gated) — "
            f"{reward_target_days - streak} to the gift card. "
            + (
                f"{freezes - freezes_used} rest-night freeze(s) left."
                if freezes - freezes_used
                else "Rest nights are fine — no chain to break."
            )
        )
    return StreakStatus(
        current_streak=streak,
        longest_streak=longest,
        multiplier=multiplier,
        today_counts=today_counts,
        freezes_remaining=max(0, freezes - freezes_used),
        reward_estimate=reward,
        message=msg,
        target_days=reward_target_days,
        gift_card_earned=gift_card_earned,
    )


@dataclass(frozen=True)
class MilestoneReward:
    topics_mastered: int
    message: str

    def as_dict(self) -> dict:
        return {"topics_mastered": self.topics_mastered, "message": self.message}


def mastery_milestone_reward(topics_mastered: int) -> MilestoneReward:
    """Reward tied to MASTERING topics, not attendance (9.5.3): the extrinsic
    system must also fire on real learning."""
    if topics_mastered <= 0:
        msg = "Master a full topic to earn a milestone — rewards follow real learning."
    else:
        msg = f"{topics_mastered} topics mastered — milestone rewards earned on real learning."
    return MilestoneReward(topics_mastered, msg)


# --------------------------------------------------------------------------- #
# Surprise / variable rewards + opt-in gift card + composed motivation state
#
# Research reconciliation (Deci/Koestner/Ryan 1999): EXPECTED, transactional
# rewards for an already-motivated learner crowd out intrinsic drive
# (overjustification). UNEXPECTED rewards and rewards that carry *competence
# information* do not. So the extrinsic layer here is (a) opt-in (autonomy), (b)
# fired by real mastery (competence), and (c) delivered as a surprise, not a
# promised wage. Loss-aversion is expressed only through the no-shame streak
# freeze (the UPenn/UCLA "slack" finding), never a punishment.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SurpriseReward:
    earned: bool
    amount: float
    reason: str
    message: str

    def as_dict(self) -> dict:
        return {
            "earned": self.earned,
            "amount": round(self.amount, 2),
            "reason": self.reason,
            "message": self.message,
        }


def surprise_reward(
    newly_mastered_count: int,
    opt_in: bool,
    cfg: AnteConfig | None = None,
) -> SurpriseReward:
    """An unexpected, competence-linked bonus that fires when you *lock in
    learning* — never for showing up. Off unless the student opted in."""
    cfg = cfg or CONFIG
    if not opt_in or not cfg.surprise_reward_enabled:
        return SurpriseReward(
            False,
            0.0,
            "rewards off",
            "Rewards are off — turn them on if they help you show up.",
        )
    if newly_mastered_count <= 0:
        return SurpriseReward(
            False,
            0.0,
            "no new mastery",
            "Lock in a topic and a surprise bonus can appear.",
        )
    amount = min(cfg.reward_cap, round(2.0 * newly_mastered_count, 2))
    return SurpriseReward(
        True,
        amount,
        f"{newly_mastered_count} newly mastered",
        f"Surprise — ${amount:.0f} for locking in {newly_mastered_count} "
        f"topic{'s' if newly_mastered_count != 1 else ''}. Money follows mastery, never logins.",
    )


def motivation_state(
    *,
    newly_mastered_count: int,
    genuine_by_day: dict[int, int],
    today_ordinal: int,
    active_days: int,
    topics_mastered_total: int,
    opt_in: bool,
    cfg: AnteConfig | None = None,
    window_days: int = 7,
) -> dict:
    """One composed motivation surface for the UI: the always-on honest signals
    (mastery momentum, forgiving consistency) plus the opt-in extrinsic layer
    (effort-gated streak, surprise + gift-card), with an honest headline."""
    cfg = cfg or CONFIG
    momentum = mastery_momentum(newly_mastered_count, window_days)
    streak = compute_streak(genuine_by_day or {}, today_ordinal, cfg)
    consistency = consistency_status(active_days)
    milestone = mastery_milestone_reward(topics_mastered_total)
    surprise = surprise_reward(newly_mastered_count, opt_in, cfg)

    # gift-card standing is only "live" when the student opted in (autonomy)
    if opt_in:
        gift = {
            "active": True,
            "amount": round(streak.reward_estimate, 2),
            "cap": cfg.reward_cap,
            "message": (
                f"${streak.reward_estimate:.0f} of a ${cfg.reward_cap:.0f}/mo gift card "
                "— effort-gated and capped, a habit primer not a wage."
            ),
        }
    else:
        gift = {
            "active": False,
            "amount": 0.0,
            "cap": cfg.reward_cap,
            "message": "Gift-card rewards are off. You can turn them on in settings.",
        }

    if newly_mastered_count > 0:
        headline = momentum.message
    elif streak.current_streak >= 3:
        headline = streak.message
    elif consistency.on_track:
        headline = consistency.message
    else:
        headline = "Mastery counts, not logins. One real session moves the needle."

    return {
        "opt_in": opt_in,
        "headline": headline,
        "momentum": momentum.as_dict(),
        "streak": streak.as_dict(),
        "consistency": consistency.as_dict(),
        "milestone": milestone.as_dict(),
        "surprise": surprise.as_dict(),
        "gift_card": gift,
        "note": (
            "Honest signals (mastery, calibration) lead; the streak/gift-card layer "
            "is opt-in, fires on real learning, and is measured so it can be cut if "
            "it ever hurts (docs/rewards-tradeoff.md)."
        ),
    }
