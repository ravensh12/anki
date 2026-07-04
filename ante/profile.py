# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The per-student study profile — everything Ante personalizes on.

This is the durable answer to "who is this student and how do they study?"
collected in the first-run onboarding (exam date first, per the product brief)
and editable afterward. It is pure data: the Qt layer persists it in the
collection config (``ante_profile``) and threads it into the dashboard and
the recalibration engine.

Design principle: nothing in the app is one-size-fits-all. The exam date sets the
schedule, the chronotype orders the day, the daily budget bounds the load, and
the reminder/reward switches respect the student's autonomy (SDT).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import CONFIG, AnteConfig

# The named day-parts a student can choose to study in, in clock order. These
# line up with rhythm.WINDOWS / sessions slot names so the same vocabulary flows
# from onboarding -> plan -> reminders.
STUDY_WINDOWS: list[str] = ["morning", "during the day", "night"]

# Chronotype -> the window where new/hard material should go (peak alertness).
# Larks peak in the morning; owls in the evening; neutral mid-day. Off-peak
# windows get lighter spaced review (see recalibrate.slot_plan).
CHRONOTYPES = ("lark", "neutral", "owl")


@dataclass
class StudyProfile:
    """Durable personalization settings. All optional so a fresh collection has
    a sane, honest default and the app still runs before onboarding."""

    exam_date: str | None = None  # ISO "YYYY-MM-DD"
    target_score: int | None = None  # 472..528
    # the player's chosen seat portrait (a bundled asset id, e.g. "avatar_3")
    avatar: str = ""
    daily_minutes: int = CONFIG.default_daily_minutes
    study_windows: list[str] = field(default_factory=lambda: list(STUDY_WINDOWS))
    chronotype: str = "neutral"
    reminders_enabled: bool = CONFIG.reminders_enabled_default
    # deliver reminders through the OS scheduler too, so the morning/night
    # bookends fire even when the app is closed (launchd / schtasks / systemd)
    background_reminders: bool = False
    quiet_start_hour: int = CONFIG.quiet_start_hour
    quiet_end_hour: int = CONFIG.quiet_end_hour
    rewards_opt_in: bool = CONFIG.rewards_opt_in_default
    onboarded: bool = False

    # ------------------------------------------------------------------ #

    @classmethod
    def from_dict(
        cls, d: dict | None, cfg: AnteConfig | None = None
    ) -> "StudyProfile":
        cfg = cfg or CONFIG
        d = d or {}
        windows = d.get("study_windows")
        if not isinstance(windows, list) or not windows:
            windows = list(STUDY_WINDOWS)
        else:
            windows = [w for w in windows if w in STUDY_WINDOWS] or list(STUDY_WINDOWS)
        chrono = d.get("chronotype")
        if chrono not in CHRONOTYPES:
            chrono = "neutral"
        exam = d.get("exam_date")
        exam = str(exam) if isinstance(exam, str) and exam.strip() else None
        target = d.get("target_score")
        target = int(target) if isinstance(target, (int, float)) and target else None
        avatar = d.get("avatar")
        avatar = str(avatar) if isinstance(avatar, str) else ""
        return cls(
            exam_date=exam,
            target_score=target,
            avatar=avatar,
            daily_minutes=_clamp_int(
                d.get("daily_minutes", cfg.default_daily_minutes),
                cfg.min_daily_minutes,
                cfg.max_daily_minutes,
                cfg.default_daily_minutes,
            ),
            study_windows=windows,
            chronotype=chrono,
            reminders_enabled=bool(
                d.get("reminders_enabled", cfg.reminders_enabled_default)
            ),
            background_reminders=bool(d.get("background_reminders", False)),
            quiet_start_hour=_clamp_int(
                d.get("quiet_start_hour", cfg.quiet_start_hour),
                0,
                23,
                cfg.quiet_start_hour,
            ),
            quiet_end_hour=_clamp_int(
                d.get("quiet_end_hour", cfg.quiet_end_hour), 0, 23, cfg.quiet_end_hour
            ),
            rewards_opt_in=bool(d.get("rewards_opt_in", cfg.rewards_opt_in_default)),
            onboarded=bool(d.get("onboarded", False)),
        )

    def as_dict(self) -> dict:
        return {
            "exam_date": self.exam_date,
            "target_score": self.target_score,
            "avatar": self.avatar,
            "daily_minutes": self.daily_minutes,
            "study_windows": list(self.study_windows),
            "chronotype": self.chronotype,
            "reminders_enabled": self.reminders_enabled,
            "background_reminders": self.background_reminders,
            "quiet_start_hour": self.quiet_start_hour,
            "quiet_end_hour": self.quiet_end_hour,
            "rewards_opt_in": self.rewards_opt_in,
            "onboarded": self.onboarded,
        }

    def in_quiet_hours(self, hour: int) -> bool:
        """True if ``hour`` (0..23) is inside the protected sleep/quiet window."""
        s, e = self.quiet_start_hour % 24, self.quiet_end_hour % 24
        if s == e:
            return False
        if s < e:
            return s <= hour < e
        return hour >= s or hour < e  # wraps midnight


def _clamp_int(v: object, lo: int, hi: int, default: int) -> int:
    try:
        n = int(v)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))
