# Ante demo script (3–5 minutes)

A tight walkthrough hitting every graded element. Commands assume the repo root
with a built dev environment (`just run` has been run once).

## 0. Setup (before recording)

```bash
just seed-deck 30                 # ~2100-card tagged MCAT deck (+ .apkg)
# optional, with HF_KEY set — the den's cinematic plates:
PYTHONPATH=. out/pyenv/bin/python -m ante.tools.gen_world --scene all --motion
```

## 1. The Rust engine change (≈60s)

"Ante makes a real change inside Anki's Rust engine, not just the Python
screens."

- Show `ante/docs/rust-change.md` (the points-at-stake order + GetTopicMastery
  RPC, why it's in Rust, files touched).
- Run the Rust + Python tests live:

```bash
just test-rust
PYTHONPATH=out/pylib ANKI_TEST_MODE=1 out/pyenv/bin/pytest pylib/tests/test_ante.py -v
```

Point out `points_at_stake_orders_by_topic_weight`,
`topic_mastery_groups_and_weights`, and
`test_points_at_stake_undo_and_no_corruption` (undo + integrity preserved).

## 2. The den (≈45s)

- Open **http://localhost:40000/_anki/ante** — you arrive _in the Emerald
  Room_: the windows keep your real clock (dawn until the morning game is
  kept, neon rain at night), Sahir at the felt, and exactly one pulled-out
  chair — **the Seat**, the single best next action.
- Point out the **bookends chips** (Morning Game / Midnight Game) and the
  **tournament clock** (D−N; the blinds — desired retention — rise as the
  final table nears).

## 3. The Table — a session on the new queue (≈45s)

- Take your seat. Cards come off the shoe in **points-at-stake order**; the
  chip stack riding each card is its live stake (exam weight × weakness).
- Before a card turns: **Check / Call / Raise** (pre-flip confidence, feeding
  calibration). Answer honestly — an _Again_ animates the House raking the
  pot.
- Every third hand is **the river**: an application item (MCQ or open-ended)
  — the thing that actually moves the Circuit. Cash out: the honest tally,
  never fake winnings.
- (Native queue proof: deck options → Display order → review order =
  **"Points at stake (Ante)"**.)

## 4. The Circuit + the Book (≈60s)

- Open **The Circuit**: four cities (New York, Monte Carlo, Havana, Macau),
  every topic a table — _roped off / open / the low table / won / unlisted_,
  with **dust** thickening over won tables as FSRS retrievability decays.
  Abstention is physical: an unlisted table is one the Circuit refuses to
  pretend about.
- Open **The Book**: below the evidence floor the board reads **NO LINE —
  insufficient action**, with the real reasons (the give-up rule). With
  evidence, the line: total + honest range + confidence + per-section lines,
  plus **your tell** (self-trust) — saying "sure" and missing pulls the line
  down.

## 5. AI: generation, checking, eval (≈45s)

```bash
PYTHONPATH=. out/pyenv/bin/python -m ante.ai.eval --offline --source <chapter.txt>
```

- Show: generated cards carry a **source quote + span**; the checker reports
  correct / wrong / bad-teaching and **emits only passing cards**; the
  answer-selection eval prints AI vs keyword vs TF-IDF.
- Note the **prompt-injection guard** (a hostile source is refused) and that with
  `ANTHROPIC_API_KEY` set it uses Claude; offline it still runs.

## 6. Proof: benchmark, calibration, experiment (≈45s)

```bash
just bench out/mcat_seed.anki2          # p50/p95/worst, all within targets
PYTHONPATH=. out/pyenv/bin/python -m ante.tools.calibrate --predictions ante/data/sample_predictions.json
PYTHONPATH=. out/pyenv/bin/python -m ante.experiment --reps-per-day 20 --days 5
```

- Benchmark: mastery query + next-card fetch under target.
- Calibration: Brier / log-loss / ECE + the reliability SVG.
- Experiment: full vs ablation vs plain at equal study time, with a CI and an
  **honest verdict** (including nulls).

## 7. The daily product: onboarding, buy-in, ritual, nudges (≈75s)

Launch the desktop app (`just run`) and sign in at the door.

- **Onboarding (date-first):** _when do you sit?_ sets the tournament clock;
  then pick your **seat portrait** (six Soul-rendered players).
- **The buy-in game:** the baseline diagnostic as a cold opening hand
  (10/section, some open-ended). It **abstains per section** below the
  evidence floor, and the count prices the **climb** (buy-in → target) into
  the daily budget. Every answer already feeds the Circuit.
- **The Back Room:** go heads-up with Sahir on a table near the bar — he
  probes exactly the rubric point you missed; win it and the plaque is yours.
  With an `OPENAI_API_KEY` set, hit **Go live** for the showpiece: a real
  streaming voice conversation (OpenAI Realtime) — Sahir asks the question
  aloud, you answer by talking, he interrupts and probes, and speaks the
  verdict. Say out loud what it proves: **the model never grades** —
  `create_response` is off, every turn is scored by the same deterministic
  rubric as a typed answer, and only then is Sahir handed a private ledger
  cue. Casino charm, no casino dishonesty. No key? The same room still runs,
  typed or spoken-in-takes.
- **Nudges:** House Rules → _Send a test nudge_ (a real macOS banner:
  "The morning game opens"); toggle background nudges and note it registers
  launchd/Task Scheduler/systemd jobs so the cue arrives before the app is
  opened (`docs/notifications.md`).

## 8. The reels + demo mode (≈30s)

- **The Midnight Game:** tonight's **last hand, replayed** — the day's
  hardest retrievals, replayed slow before sleep; on exam eve, **The Run** —
  a documentary cut only from logged history, ending on the honest line.
- From the door: **Just show me the demo** — a time-travellable den on
  synthetic data, with jumps (First night → The Bridge → The Sharpen → Final
  Table eve).

## Close

"One exam, one shared Rust engine, a real engine change, three scores we can
back up — or honestly abstain from — and a nightly game against the only
opponent that matters: the forgetting curve. Same learning, half the time.
The House stopped winning."
