# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ante tunable constants (PRD Section 17).

All knobs live here so behaviour is configurable per the engagement guardrail
(PRD 6.5): if gating proves too rigid, loosen STRENGTH_FRACTION / MASTERY_BAR
rather than hard-coding. Values can be overridden from the environment
(ANTE_<NAME>) so an experiment or a power user can retune without code edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

PRODUCT_NAME = "Ante"
EXAM = "MCAT"


@dataclass(frozen=True)
class AnteConfig:
    # --- mastery thresholds (PRD 6.1, 17) ---
    # fraction of a topic's cards that must be "at strength" for mastery
    strength_fraction: float = 0.85
    # FSRS retrievability counted as "at strength"
    r_threshold: float = 0.90
    # performance accuracy required for topic mastery (Bloom's bar)
    mastery_bar: float = 0.80
    # below this recent performance, an active topic is routed to corrective
    corrective_bar: float = 0.60
    # engagement guardrail (PRD 6.5): if enabled, passing the application check
    # alone masters a topic (skip the strength-drilling requirement)
    test_out_enabled: bool = False
    # mastery is shown from QUIZZES + OPEN-ENDED answers only (not flashcard
    # self-ratings). FSRS card-strength stays a separate "retention" signal. Set
    # True to also require recall strength (the older, stricter Bloom gate).
    mastery_requires_strength: bool = False
    # an open-ended answer scores 0..1; at/above this it counts as "correct" for
    # the Bloom re-test loop (due/re-assess). Partial credit still feeds accuracy.
    open_pass_score: float = 0.6

    # --- readiness abstention (give-up rule; PRD 7.4, 17) ---
    # a first, honest (wide-range, low-confidence) reading is earned after a
    # reachable amount of real evidence; the range narrows and confidence rises
    # with more. Kept a real bar (never a number from nothing), but low enough
    # that a motivated week of study unlocks a projection.
    giveup_min_reviews: int = 60
    giveup_min_coverage: float = 0.35

    # --- sessions (PRD 9.2, 17) ---
    default_session_minutes: int = 10
    seconds_per_card: float = 8.0

    # --- AI (PRD 8.2, 17) ---
    # minimum checker pass rate before any generated card is shown
    ai_card_cutoff: float = 0.60

    # --- consistency streak + reward (PRD 9.5, 17) ---
    # minimum genuinely-attempted reviews for a day to count (effort-gate)
    streak_min_reviews: int = 15
    # per-card response time (ms) below which a review is not "genuine" (anti-gaming)
    streak_min_response_ms: int = 800
    # rest-day allowances that do not break the streak
    streak_freezes_per_month: int = 4
    # monthly gift-card value cap (a habit primer, not a wage for studying)
    reward_cap: float = 20.0

    # --- coverage (PRD 4.2) ---
    # an outline topic counts as "covered" once it has >= this many cards
    coverage_min_cards: int = 1

    # --- onboarding + recalibration (personalization; Principle 1) ---
    # baseline daily study budget (minutes) before exam-date recalibration. ~2h
    # is a realistic MCAT commitment; the whole "time back" pitch is that these
    # focused minutes beat a crammer's 4+ scattered hours, not that prep is trivial.
    default_daily_minutes: int = 120
    min_daily_minutes: int = 15
    max_daily_minutes: int = 300
    # FSRS desired retention is ramped between these as the exam approaches
    # (Cepeda 2008: the optimal spacing gap shrinks as the retention interval
    # shrinks, so we tighten reviews -> raise target retention -> near test day)
    retention_floor: float = 0.85
    retention_ceiling: float = 0.94
    # begin ramping desired retention up once within this many days of the exam
    retention_ramp_days: int = 60

    # --- confidence calibration -> honest interval (Principle 4; Koriat & Bjork) ---
    # minimum confidence-rated answers before calibration moves any interval
    calibration_min_rated: int = 5
    # max downward + widening penalty (in accuracy points) applied when the
    # student is systematically OVER-confident (says "sure", gets it wrong)
    calibration_penalty_max: float = 0.12

    # --- data capture / response-time classification (ms) ---
    # a WRONG answer faster than this looks careless (a slip, not a gap)
    careless_ms: int = 3000
    # a CORRECT answer faster than this looks fluent/automatic (real recall)
    fluent_ms: int = 7000

    # --- notifications / reminders (Principle 2; Gollwitzer implementation intentions) ---
    reminders_enabled_default: bool = True
    # no notifications fire inside the quiet window (protects sleep = consolidation)
    quiet_start_hour: int = 22
    quiet_end_hour: int = 7

    # --- motivation (autonomy-first, overjustification-aware; Deci/Ryan 1999) ---
    # rewards are OPT-IN by default (autonomy support lowers overjustification risk)
    rewards_opt_in_default: bool = False
    # surprise/variable rewards on mastery (unexpected rewards do NOT crowd out
    # intrinsic motivation in Deci 1999; expected transactional wages do)
    surprise_reward_enabled: bool = True

    # --- generative studio (Higgsfield + ElevenLabs; offline-first) ---
    # media generation runs only against the student's own keys, budget-capped;
    # with no keys everything renders via the deterministic offline engraver
    studio_enabled: bool = True
    studio_daily_cap: int = 24
    studio_monthly_cap: int = 400

    # --- the Palace (generated mnemonics; dual coding on measured leeches) ---
    # a card becomes a leech candidate at this many lapses (Anki's default is 8;
    # we intervene earlier because the fix is a mnemonic, not suspension)
    palace_min_lapses: int = 3
    # cap on total palace scenes kept (oldest leech wins; keeps cost bounded)
    palace_max_assets: int = 48

    # --- the Viva (oral mastery test-out; generation effect) ---
    # overall spoken-defense score needed to seal mastery
    viva_pass_score: float = 0.75
    # follow-up probes the examiner may ask (each targets a missed rubric point)
    viva_probe_rounds: int = 2

    # --- Dream Seed (Last Light consolidation reel) ---
    dreamseed_scenes: int = 5

    @classmethod
    def from_env(cls) -> "AnteConfig":
        kwargs: dict[str, object] = {}
        for f in fields(cls):
            env = os.environ.get(f"ANTE_{f.name.upper()}")
            if env is None:
                continue
            if f.type == "int":
                kwargs[f.name] = int(env)
            elif f.type == "float":
                kwargs[f.name] = float(env)
            elif f.type == "bool":
                kwargs[f.name] = env.strip().lower() in ("1", "true", "yes", "on")
            else:
                kwargs[f.name] = env
        return cls(**kwargs)  # type: ignore[arg-type]

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# module-level default instance
CONFIG = AnteConfig.from_env()
