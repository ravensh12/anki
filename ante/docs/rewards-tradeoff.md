# The consistency streak — honest tradeoff (recorded verbatim, PRD 9.5.4)

Ante ships **two** motivation surfaces: a mastery-momentum signal (always on,
thesis-aligned) and a **monthly consistency streak with a gift-card reward** (the
extrinsic layer). The streak is included by product decision; the mitigations
exist because it sits in tension with Principle 4 ("honesty over encouragement"), and
that tension is documented on purpose so it can be defended rather than hidden.

## The tradeoff (PRD 9.5.4, verbatim)

This feature sits in explicit tension with Principle 4 and the learning-science
evidence, and that is acknowledged rather than hidden:

- Login/attendance streaks reward _attendance_, not learning, and can incentivize
  minimum-effort logins (the cram mindset in disguise). The effort-gate (9.5.2) is
  the mitigation, and it is imperfect.
- Cash rewards risk the **overjustification effect** — paying already-motivated
  students to do something they were intrinsically motivated to do can _replace_
  the internal drive, so behavior may drop once the reward stops. The target user
  is _already motivated_, which is exactly the population most exposed to this
  effect. The cap-and-sunset (9.5.3) and the mastery-linked rewards are the
  mitigations.
- Streak pressure can stress an anxious population and punish rest days. The
  freezes and no-shame rules (9.5.3) are the mitigations.

**The test that still governs every reward in the app:** _does it fire when
learning is real, or just when attendance is logged?_ The mastery-momentum signal
passes cleanly. The gift-card streak passes only to the degree its effort-gate
holds — which is why it is measured, capped, and killable.

## Why the enhanced design is defensible (the research, updated)

The original doc treated the extrinsic layer as a reluctant compromise. A closer
read of the evidence supports a _bounded_ version rather than a blanket ban:

- **The overjustification effect is specific, not blanket.** Deci, Koestner &
  Ryan's (1999) meta-analysis found that _expected, tangible, performance- or
  engagement-contingent_ rewards reduce free-choice intrinsic motivation — but
  _unexpected_ rewards and _task-noncontingent_ rewards do **not**. Ante's
  surprise, mastery-triggered bonus is deliberately the non-harmful kind.
- **Autonomy is the buffer.** Self-determination theory (and gamification
  reviews, e.g. Hanus & Fox 2015) show controlling, mandatory reward structures
  are what backfire. Making the entire layer **opt-in** converts it from a
  controlling incentive into a self-chosen support.
- **Rewards that carry competence information help.** When a reward marks genuine
  mastery (not attendance), it _supplies_ the SDT competence need instead of
  crowding it out — which is exactly why the layer is pinned to the mastery
  signal and shown beside the honest readiness/calibration numbers.
- **Loss-aversion works when it's slack, not shame.** The streak-freeze is built
  on the finding (UPenn/UCLA; Duolingo's own writeup) that giving people a little
  slack sustains goals better than rigid chains.

This is still a tradeoff, not a free lunch: the target user is already motivated,
which is the population most exposed to overjustification. So the decision rule
below is unchanged — the layer stays only as long as the telemetry says it helps.

## How the mitigations are implemented

| Mitigation (PRD 9.5.3)                        | Where                                                                                                                                                                                                                                                     |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Effort-gated, not attendance-gated            | `rewards.day_counts` — a day counts only with ≥ `STREAK_MIN_REVIEWS` genuine reviews; the desktop bridge counts a review as genuine only if its response time ≥ `STREAK_MIN_RESPONSE_MS`                                                                  |
| Forgiving consistency / freezes               | `rewards.compute_streak` absorbs gaps with up to `STREAK_FREEZES_PER_MONTH` freezes; never "you broke your chain"                                                                                                                                         |
| Reward mastery too                            | `rewards.mastery_milestone_reward` fires on topics mastered, not attendance                                                                                                                                                                               |
| No shame mechanics                            | streak messages are neutral ("No streak yet — that's fine…"), never punitive                                                                                                                                                                              |
| Cap and sunset                                | reward is capped at `REWARD_CAP` and scales toward a monthly target, resetting monthly                                                                                                                                                                    |
| Pure attendance banned                        | `rewards.reward_is_allowed` still bans `login_streak` / `daily_login_bonus`                                                                                                                                                                               |
| **Opt-in (autonomy)**                         | `profile.rewards_opt_in` defaults **False**; the whole extrinsic layer is off unless the student turns it on. Autonomy support is the strongest SDT buffer against overjustification                                                                      |
| **Surprise, not a wage**                      | `rewards.surprise_reward` fires _unexpectedly_ on newly-mastered topics. Deci, Koestner & Ryan (1999): _unexpected_ and _task-noncontingent_ rewards are the ones that do **not** reduce intrinsic motivation; expected, performance-contingent wages are |
| **Loss-aversion via "slack," not punishment** | streak freezes are framed as permitted rest (UPenn/UCLA slack finding; Duolingo streak-freeze), never a broken-chain penalty                                                                                                                              |
| **Rides beside honest signals**               | the motivation surface always sits next to readiness + calibration, so a reward carries competence information rather than replacing it                                                                                                                   |

## Falsification / measurement (PRD 9.5.5)

Because the evidence cuts against this feature, it must be instrumented and be
killable. The decision rule: **if the reward group shows equal-or-worse learning,
or a post-reward engagement collapse relative to the no-reward group, cut or
restructure the reward.** The A/B design (streak layer ON vs OFF, both keeping the
mastery-momentum signal) is specified in `ante/experiment.py`
(`run_streak_experiment`), with the primary tell being **retention after the
reward period ends** (the overjustification signature).
