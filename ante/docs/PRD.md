# Ante — Product Requirements Document

|                  |                                                                                 |
| ---------------- | ------------------------------------------------------------------------------- |
| **Product**      | Ante — a mastery-gated MCAT study app, forked on Anki                           |
| **Owner**        | Shravan Venkat                                                                  |
| **Status**       | v4 (Mnemopolis world + generative Studio) implemented; iOS companion scaffolded |
| **Exam**         | MCAT (472–528; four sections each 118–132)                                      |
| **License**      | AGPL-3.0-or-later (fork of Anki by Ankitects Pty Ltd; some parts BSD-3-Clause)  |
| **Last updated** | 2026-07-02                                                                      |

---

## 1. Overview

Ante is a fork of Anki that adds the three layers Anki structurally lacks:
topic-level **mastery-gating**, the **memory→performance bridge**, and **honest
readiness**. It is built for the motivated-but-inconsistent pre-med: someone who
has the drive but studies on the wrong schedule (cramming, lopsided weeks) and
mistakes _coverage_ for _mastery_.

The product thesis, in one sentence:

> Anki tells you whether you remember a card. Ante tells you whether you can
> use it on a real MCAT passage, whether you've actually mastered a topic or just
> walked past it, and — honestly, with a range — what you'd score today.

The headline feature is **"time back"**: instead of rewarding total hours, the app
takes a fixed daily time budget (e.g. ~75 minutes split across the day) and spends
it on the highest-value, weakest topics first — so a disciplined schedule feels
like it _gives time back_ rather than taking it.

---

## 2. Problem statement

Pre-meds are not short on motivation; they are short on a system that turns
motivation into the right daily behavior. Three failure modes dominate:

1. **Wrong schedule.** The same study minutes produce wildly different retention
   depending on spacing. Cramming is the worst case, and it is the default.
2. **Coverage ≠ mastery.** Calendar-driven prep celebrates "finished biochem"
   without proof of durable recall, so students arrive at test day with a thick
   binder and a thin memory.
3. **No honest signal.** Students track "hours studied" and "content covered" —
   both misleading — and have no calibrated sense of where they actually stand,
   which drives panic-cramming and avoidance.

Anki solves spacing (FSRS) and the due-queue, but has no concept of a _topic_, no
mastery-gating, no memory→performance bridge, and no readiness score. Those gaps
are the product.

---

## 3. Goals and non-goals

### Goals

- G1. Make the disciplined, distributed schedule the path of least resistance.
- G2. Schedule by **exam value × weakness**, not just per-card recall.
- G3. Measure and display **three separate** signals (memory, performance,
  readiness), each with a range.
- G4. Never show a number the data can't support (explicit abstention).
- G5. Generate study material from real sources, safely and traceably.
- G6. Prove every claim with re-runnable tests and held-out evaluation.

### Non-goals

- N1. Replacing Anki's spacing engine (we extend FSRS, not replace it).
- N2. Claiming Bloom's "2 sigma" outcome (we cite the defensible ~0.5 SD).
- N3. Section-by-section MCAT content strategy or tutoring.
- N4. A validated score concordance from a week of data (we grade the bridge
  steps and stay honest).

---

## 4. Target users

- **Primary — "the driven pre-med."** Motivated, conscientious, already studies a
  lot; loses consistency at the moment of deciding _what to do right now_, and
  can't tell memorization from mastery. Ante removes the decision and proves
  progress.
- **Secondary — the anxious test-taker.** Studies in fear-driven bursts because
  they don't know where they stand. Ante replaces dread with a calibrated
  standing and a single best next action.

---

## 5. Differentiation (vs. plain Anki + a premade deck)

| Capability                                    | Anki | Ante                    |
| --------------------------------------------- | ---- | ----------------------- |
| Spaced repetition / FSRS memory               | Yes  | Yes (reused)            |
| Due-queue decides next card                   | Yes  | Yes                     |
| Order by **topic exam-value × weakness**      | No   | **Yes (engine change)** |
| Per-topic **mastery query**                   | No   | **Yes (engine change)** |
| Coverage-vs-mastery, with abstention          | No   | **Yes**                 |
| Memory→performance bridge                     | No   | **Yes**                 |
| Honest readiness score + range + give-up rule | No   | **Yes**                 |
| Time-budgeted "time back" planner             | No   | **Yes**                 |
| AI card generation with provenance + checking | No   | **Yes**                 |

---

## 6. Functional requirements

### 6.1 Engine: scheduling and mastery (the required Rust change)

- **FR-E1 — Points-at-stake order.** A new review order sorts due cards by
  `topic_weight × (1 − retrievability)`, highest first. Degrades gracefully to
  "highest-yield topic first" when FSRS data is absent. Selectable from deck
  options.
- **FR-E2 — Mastery query.** A backend RPC returns, per topic: total cards,
  studied cards, mastered cards (retrievability ≥ threshold), average recall, and
  coverage — fast enough to power the dashboard on 50k cards.
- **FR-E3 — Safety.** The new order changes presentation only; answering remains
  fully undoable and must not corrupt the collection.
- **FR-E4 — Shared engine.** Both features live in Rust behind the single
  protobuf dispatch seam, so they reach every client (and a future phone app).

_Acceptance:_ ≥3 Rust unit tests + ≥1 Python integration test; undo + integrity
proven; a one-page "why Rust" note and touched-files list. (Implemented:
`rslib/src/scheduler/topics.rs`, `storage/sqlite.rs`, `storage/card/mod.rs`;
proof in `ante/docs/rust-change.md`.)

### 6.2 Topic taxonomy and coverage

- **FR-C1 — Taxonomy.** Encode the AAMC outline as `mcat::section::topic` tags
  with per-section exam weights (`ante/data/mcat_outline.json`).
- **FR-C2 — Coverage map.** Show covered vs total topics, weighted by exam value,
  per section.
- **FR-C3 — Abstention.** If weighted coverage < 50% or a high-weight section is a
  blind spot, the app refuses a readiness score and states why.
- **FR-C4 — Seed deck.** Provide a generator that produces a topic-tagged deck and
  an importable `.apkg`.

### 6.3 Models (separate, honest)

- **FR-M1 — Memory.** Report calibration (Brier, log-loss, ECE) + a reliability
  chart on held-out reviews; every aggregate recall carries a confidence range.
- **FR-M2 — Performance.** Predict correctness on _new_ exam-style questions from
  topic mastery, difficulty, timing, coverage. Must beat a memory-only baseline.
- **FR-M3 — Paraphrase test.** For sampled cards, compare card recall vs accuracy
  on reworded questions and report the gap (proves we measure application, not
  wording).
- **FR-M4 — Readiness.** Map performance → MCAT scale with a point estimate,
  range, confidence, reasons, last-updated, and a written give-up rule
  (≥200 reviews and ≥50% coverage).

### 6.4 Web app (desktop, on the shared engine)

- **FR-W1 — Review loop** on the MCAT deck using the new order.
- **FR-W2 — Dashboard** at `/_anki/ante`: the three scores with ranges, the
  coverage map, and the points-at-stake topic ranking.
- **FR-W3 — Time-back planner.** Set a daily budget + slots; show the cards each
  slot buys, whether the budget covers today's load, and the single best next
  topic.
- **FR-W4 — Offline-capable.** The page runs and scores with AI switched off.

### 6.5 AI (Claude, provider-isolated)

- **FR-AI1 — Generation.** Produce cards from a real source; each card carries a
  traceable source quote + span.
- **FR-AI2 — Provider isolation + fallback.** Use Anthropic Claude when a key is
  present; otherwise a deterministic offline provider. The app never hard-depends
  on AI.
- **FR-AI3 — Injection guard.** Detect and refuse hostile/instruction-injecting
  sources.
- **FR-AI4 — Quality gate.** Classify generated cards (correct / wrong /
  bad-teaching) against a 50-pair gold set with a pre-declared cutoff; emit only
  passing cards.
- **FR-AI5 — Eval before users.** Compare AI vs keyword and TF-IDF baselines
  (accuracy + wrong-rate); run before any card is shown.
- **FR-AI6 — Leakage check.** Flag test items that leaked into training data.

### 6.6 Study-feature experiment

- **FR-X1.** Compare three arms at equal study time — full (feature on),
  ablation (feature off), plain Anki — on a pre-registered metric, reporting a
  range and null results honestly.

---

## 7. Non-functional requirements

- **NFR-1 — Latency (desktop):** next-card fetch p95 < 100 ms; dashboard first
  load p95 < 1 s; dashboard refresh p95 < 500 ms. (`just bench` reports
  p50/p95/worst.)
- **NFR-2 — Scale:** correct and within latency on a 50k-card deck.
- **NFR-3 — Reliability:** zero corrupted collections across crash/undo tests.
- **NFR-4 — Honesty (hard constraint):** never display a readiness number without
  evidence, range, coverage %, confidence, last-updated, reasons, and the
  give-up rule. A dressed-up guess is an automatic fail.
- **NFR-5 — Reproducibility:** held-out evaluation and a re-runnable test setup.
- **NFR-6 — Licensing:** AGPL-3.0-or-later with credit to Anki.

---

## 8. Architecture

```
Rust engine (rslib)  ── points-at-stake order + GetTopicMastery RPC
   │  single seam: Backend::run_service_method(service, method, bytes)
   ├─ Python (pylib)  ── models, coverage, AI, benchmark, experiment (ante/)
   └─ Web UI (mediasrv) ── /_anki/ante dashboard on the same engine
```

- Engine change rationale and merge-risk: `ante/docs/rust-change.md`.
- Ante logic is an importable package (`ante/`) so pure logic is unit-
  tested without Anki.
- Mobile deferred; the shared seam is preserved for a future phone client.

---

## 9. Success metrics

- **Adoption/consistency (product):** 14- and 30-day return rate; sessions logged
  per active day; fraction of due load cleared within the daily budget.
- **Learning (model):** memory calibration (Brier/ECE); performance model beats
  baseline on held-out log loss; measured paraphrase gap > 0 (real bridge).
- **Honesty (trust):** % of sessions where the app correctly abstains when data is
  insufficient; readiness range width vs coverage.
- **Engine (quality):** p50/p95/worst within NFR-1 on the 50k deck.

---

## 10. Milestones

| Milestone                 | Scope                                                                                                                                                    | Status      |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| M1 — Engine + scaffolding | Rust change, proto, bindings, tests; seed deck; coverage                                                                                                 | Done        |
| M2 — Models + web UI      | calibration, bridge, readiness; dashboard + time-back                                                                                                    | Done        |
| M3 — AI                   | generation, checker, eval, baselines, offline fallback                                                                                                   | Done        |
| M4 — Proof                | leakage, `just bench`, A/B harness, demo + docs                                                                                                          | Done        |
| M5 — Personalization      | onboarding, recalibration, reminders, motivation (v2)                                                                                                    | Done        |
| M6 — Daily product        | baseline diagnostic, bookends ritual, OS notifications, demo tour, gift-card streak (v3)                                                                 | Done        |
| M7 — iOS companion        | SwiftUI app + notification scheduler scaffolded; shared-engine xcframework + sync = next                                                                 | In progress |
| M8 — Mnemopolis + Studio  | generative-media Studio (Higgsfield + ElevenLabs, offline engraver); the Palace, the Viva, the living city (dashboard retired), Dream Seed + Documentary | Done        |

---

## 11. Risks and mitigations

| Risk                                                       | Mitigation                                                      |
| ---------------------------------------------------------- | --------------------------------------------------------------- |
| Mastery-gating depresses engagement (Kulik 1990 guardrail) | Keep gates soft; add test-out paths; watch completion           |
| Readiness over-trusted                                     | Mandatory range + confidence + give-up rule; abstain by default |
| AI hallucination / wrong cards                             | Quality gate blocks non-correct cards; provenance required      |
| Prompt injection via sources                               | Injection guard refuses hostile sources                         |
| Data leakage inflates scores                               | Leakage scanner gates training data                             |
| No phone app caps grade at 70%                             | Deliberate scope; shared seam preserved for later               |

---

## 12. Open questions

- Where to source a large, license-clean MCAT deck (vs. AI-from-sources)?
- Exact mastery threshold and coverage minimum — tune on real review logs.
- Calibrating the readiness heuristic once longitudinal data exists.
- Which study feature to formally A/B with real users first (interleaving vs
  points-at-stake ordering).

---

## 13. Out of scope (v1)

- Native iOS/Android apps and two-way sync (planned, not built).
- A validated, student-calibrated score concordance.
- MCAT content tutoring or section strategy.
- Multi-exam support (LSAT/GMAT/USMLE) — architecture allows it; not built.

---

## 14. v2 additions — personalization, notifications & honest motivation

These extend the v1 thesis; each still traces to a design principle and ships with tests.

### 14.1 Exam-date onboarding + recalibration (Principle 1)

- **FR-P1 — Date-first onboarding.** First run asks the exam date (then target,
  chronotype, daily budget, reminder/reward switches) before anything else.
- **FR-P2 — Recalibration engine** (`recalibrate.py`): from the date, recompute
  (a) recommended daily minutes (remaining mastery work ÷ study-days-left +
  upkeep), (b) FSRS **desired-retention ramp** (floor→ceiling as the exam nears;
  Cepeda 2008), (c) a **review-interval cap** at test day, and (d) the shape of
  the day (peak-window placement by chronotype). Applied to the deck's FSRS
  config in `qt/aqt/ante.py::apply_recalibration`.
- **FR-P3 — Durable profile** (`profile.py`, `ante_profile` col config).

### 14.2 When-to-study notifications (Principle 2)

- **FR-N1 — Schedule** (`reminders.py`): cue-anchored, learning-science-timed
  (morning retrieval, midday review, pre-sleep encode), no-shame copy, quiet-hours
  suppression.
- **FR-N2 — Delivery** (`qt/aqt/ante_reminders.py`): system-tray notifications
  via a once-a-minute timer; in-app toast fallback; opt-in.

### 14.3 Mastery from application only + open-ended (Principle 3)

- **FR-A1 — Application-gated mastery.** Mastery is shown from quizzes +
  open-ended accuracy, never flashcard self-ratings (`mastery._meets_mastery`;
  FSRS strength is a separate retention signal, re-enable via
  `mastery_requires_strength`).
- **FR-A2 — Open-ended items** (`openended.py`, `data/open_ended_items.json`):
  offline rubric/keyword grading with partial credit + model-answer feedback;
  pooled with MCQ into one signal (`applied.py`).
- **FR-A3 — Comprehension Atlas** (`comprehension.py`): complete per-topic
  comprehension with calibration-adjusted bands + an overall reading paired with
  the evidenced fraction of exam weight.

### 14.4 Confidence-calibrated honesty (Principle 4)

- **FR-H1 — Confident-but-wrong lowers the interval.** Over-confidence
  (`metacognition.overconfidence_penalty`) lowers the point estimate and drops the
  lower bound of readiness + comprehension. A **self-trust** meter surfaces it.

### 14.5 Full data capture

- **FR-D1 — Timing on everything.** Per-card (revlog) and per-item elapsed time
  feed a fluent/effortful/careless/struggled classification (`analytics.py`).

### 14.6 Honest motivation (Principle 4, bounded)

- **FR-R1 — Opt-in extrinsic layer** (`rewards.py`): effort-gated streak +
  freezes + **surprise** mastery rewards; off by default; measured + killable per
  `docs/rewards-tradeoff.md`.

---

## 15. v3 additions — the baseline, the ritual, notifications that fire, and iOS

v3 turns the pieces into a product a student opens every day. Each still traces
to a design principle and ships with tests (`ante/tests/`).

### 15.1 The Baseline Diagnostic (Principle 3 — Bloom's formative check)

- **FR-B1 — Onboarding diagnostic.** After the exam date + target, first-run
  offers a short formative test: up to 10 items per section, a couple
  **open-ended**, sampled across the highest-weight topics
  (`diagnostic.build_diagnostic`; deterministic per seed). Skippable.
- **FR-B2 — Honest baseline.** Answers pool MCQ correctness + open-ended partial
  credit per section into a Wilson-banded accuracy and a snapshot score
  (`diagnostic.summarize_diagnostic`). A section below the evidence floor is
  **not scored**; the overall baseline appears only when every section is
  scoreable — partial diagnostics stay partial (never extrapolated).
- **FR-B3 — It's the first formative loop, not a silo.** Diagnostic answers are
  recorded through the _same_ quiz/open response logs, so they immediately feed
  mastery, comprehension, calibration and readiness. Nothing is wasted.
- **FR-B4 — The climb.** The baseline → target gap sets a **bounded** effort
  multiplier on the daily budget (`recalibrate._target_gap_factor`, ≤ +40%); a
  gap ≤ 0 never manufactures urgency. Today shows the score's delta from
  baseline.

### 15.2 The daily bookends — First Light / Last Light (Principles 1 + 2)

- **FR-K1 — Ritual state** (`ritual.py`): from today's genuine reviews by hour,
  report each bookend's done/next state with no-shame copy. Small-hours study
  earns no credit (protecting sleep is part of the design). Surfaced as the
  Today screen's bookends strip.

### 15.3 Notifications that fire when the app is closed (Principle 2)

- **FR-N3 — Native desktop delivery.** macOS gets a real Notification Center
  banner (`osascript`); Windows/Linux the tray; in-app toast fallback
  (`qt/aqt/ante_reminders.py`). "Send a test" in Settings.
- **FR-N4 — OS-scheduled delivery** (`os_notify.py`, opt-in): registers the
  day's cues with launchd / Task Scheduler / systemd so the morning nudge
  arrives before the app is ever opened. Reversible, quiet-hours safe, evergreen
  copy. Full design: `docs/notifications.md`.

### 15.4 Demo mode — the product film (adoption)

- **FR-DM1 — In-app cinematic tour.** A self-advancing, 10-scene product tour
  built from the real UI vocabulary (design principles, the ritual, the diagnostic, the
  session, the Atlas, honest readiness, the streak/gift card, one-engine iOS),
  launchable from the login screen and Settings. Reduced-motion aware.
- **FR-DM2 — Populated demo account.** `tools/seed_demo.py` seeds a realistic
  study history so the dashboard shows a live instrument, not empty state.

### 15.5 Gift-card streak, surfaced (Principle 4, bounded)

- **FR-G1 — Everyday-login-streak framing, effort-gated in substance.** A
  prominent daily streak + gift-card progress bar on Today, opt-in. A day counts
  only on genuine study (`rewards.day_counts`), never on app-opens — the
  everyday-streak _feel_ without the hollow-metric trap the Brainlift warns
  against.

### 15.6 Mobile: iOS companion (replaces the Android plan)

- **FR-IOS1 — SwiftUI companion** (`ios/`): onboarding, Today with the bookends,
  Atlas, Session, Plan, and a `UNUserNotificationCenter` scheduler that makes the
  morning/night ritual first-class. Consumes the shared Rust engine as an
  xcframework over the same protobuf seam (documented next step;
  `MockEngine` today). See `docs/mobile-and-sync.md`. The prior AnkiDroid
  companion proved the shared-engine approach; iOS is the v1 phone target.

---

## 16. v4 — Mnemopolis: the generative world (the app becomes a place)

v4 makes two moves at once: (1) the dashboard is retired — the entire interface
is now a living city rendered from the engine's honest signals; and (2) a
provider-isolated **generative-media Studio** (Higgsfield + ElevenLabs, offline
engraver fallback) manufactures bespoke study media from the student's own data.
Every feature is opt-in, budget-capped, killable, and still scores with AI OFF.
Each traces to a design principle and ships with pure-logic tests
(`ante/tests/test_studio|palace|viva|mnemopolis|reels.py`).

### 16.1 The Studio (provider isolation for media; Principle 5 — AI-optional)

- **FR-S1 — One media seam** (`ante/ai/studio.py`): `still` (Higgsfield
  Soul), `motion` (DoP image→video), `speech` (ElevenLabs / OpenAI TTS),
  `talking_head` (Speak v2), `transcribe` (Scribe / Whisper). Content-addressed
  cache under the collection media dir; nothing generates twice.
- **FR-S2 — Offline-first.** With no keys, a deterministic **engraver** renders
  parchment SVG plates; audio/video simply absent. The app is beautiful and
  fully functional with AI off (same rule as card-generation AI).
- **FR-S3 — Budget + consent.** `studio_daily_cap` / `studio_monthly_cap` ledger
  (`config.py`, `ANTE_STUDIO_*`); over budget degrades to offline. Runs only
  against the student's own keys, on worker threads (never blocks review).

### 16.2 The Palace (Principle 3 — dual coding on measured leeches)

- **FR-P4 — Generated mnemonics.** `ante/palace.py` picks leeches (lapses ≥
  `palace_min_lapses`, weakest first), Claude designs a scene mapping each fact
  to one object, **every anchor fact is verified against the card's own text**
  (`verify_spec`; unsupported anchors dropped, empty → faithful offline spec),
  and the Studio renders it. Shown on the card's answer side + the Archive.

### 16.3 The Viva (Principle 3 — production over recognition)

- **FR-V1 — Oral test-out.** `ante/viva.py` turns the dormant
  `test_out_enabled` guardrail into an examination: the student explains a topic
  (spoken → `Studio.transcribe`, or typed), graded by the existing
  `openended.py` rubric machinery; Socratic probes target the missed rubric
  point; the attending's verdict is a cached Speak clip. A pass feeds the same
  open-response log as every other application item — mastery/readiness update
  with zero new scoring. One defense per topic per day (corrective loop, not a
  slot machine).

### 16.4 Mnemopolis (Principles 2, 3, 4 — the world replaces the dashboard)

- **FR-W5 — The city IS the app** (`ante/mnemopolis.py`, `web/atlas.html`):
  four districts (sections), one building per topic; state = mastery (scaffold /
  raised / cracked / lit), **fog = FSRS decay**, **unmapped = no evidence**
  (abstention as geography). Civic places carry the old dashboard's functions:
  Examination Hall (Viva), Observatory (readiness; **fogged shut** while it
  abstains), Archive (Palace), Cinema (film / Documentary), Meridian (ritual +
  Dream Seed), Surveyor's Gate (diagnostic). The **waypoint** lights exactly one
  path to the single best next action (Principle 2 preserved without a nav bar).
- **FR-W6 — Local-first render.** The city composites locally from the
  `GetTopicMastery` RPC + mastery/comprehension; Higgsfield paints district
  plates once and a weekly DoP flyover — day-to-day costs **zero API calls**.

### 16.5 Dream Seed + The Documentary (Principles 1, 4 — riders)

- **FR-R2 — Dream Seed** (`ante/dreamseed.py`): a Last-Light reel of today's
  hardest retrievals (by `analytics.py` classification), replayed with slow
  narration; assembled in-page from cached assets (near-zero marginal cost).
- **FR-R3 — The Documentary** (`ante/documentary.py`): an exam-eve montage
  built only from logged history; the verdict chapter honours the same
  range/abstention as readiness — it never promises a score.

_Acceptance:_ 55 new pure-logic tests (offline provider), all green; the world
renders and scores with every provider key absent; the Studio never blocks the
review loop; readiness/abstention rules hold in the Observatory **and** the
Documentary. Media providers: `docs/mobile-and-sync.md` unchanged; keys are
`HF_KEY`/`HF_API_KEY`+`HF_API_SECRET`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`.
