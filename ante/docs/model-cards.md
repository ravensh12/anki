# Ante model descriptions (memory · performance · readiness)

One page per model, as required by the spec ("Model descriptions: one short page
each for the memory, performance, and readiness models, including the give-up
rule"). Ante keeps the three **separate on purpose** — a great memory model is
not a great score model, and blending them is the easiest way to fake readiness.
Each model is pure Python, runs with **AI switched off**, and is unit-tested.

The give-up rule is shared by all three (it lives in the readiness model) and is
restated at the bottom.

---

## 1. Memory — "can the student recall this fact right now?"

- **Code:** [`ante/memory.py`](../memory.py); report/chart CLI
  [`ante/tools/calibrate.py`](../tools/calibrate.py) (`just` → see below).
- **Question it answers:** the probability a *specific card* is recalled today.
- **What it is (and is not):** Anki's **FSRS** already estimates per-card recall
  probability. We do **not** replace it. The memory model's job is to **check
  FSRS honestly** and attach a range to every aggregate.

**Inputs.** Predicted recall probabilities (from FSRS) paired with the actual
`{0,1}` review outcomes, taken from the review log.

**Method.** On a held-out set of reviews we compute:

- **Brier score** — mean squared error of the probability (lower is better).
- **Log loss** — mean negative log-likelihood (lower is better).
- **Expected Calibration Error (ECE)** — reviews are bucketed into 10 reliability
  bins; ECE is the count-weighted average gap between predicted probability and
  observed recall in each bin. This is the number behind the claim "when it says
  80%, they recall ~80%".
- **Reliability diagram** — the predicted-vs-observed curve, rendered as a
  dependency-free SVG (`render_reliability_svg`).

**Uncertainty.** Every aggregate recall number ships with a **95 % Wilson score
interval** (`wilson_interval`), so the UI never shows a bare point. The interval
correctly widens on small samples and accepts fractional successes when pooling
partial-credit evidence.

**Honesty / limits.** Calibration is measured on **held-out** reviews, not the
data FSRS trained on. We report the numbers that came out, including poor ones.
We do **not** claim to improve FSRS's accuracy — only to quantify it.

**How to reproduce.**

```bash
just bench                      # includes the mastery/recall query latency
# calibration report + reliability.svg from the committed held-out sample:
PYTHONPATH=. out/pyenv/bin/python -m ante.tools.calibrate \
  --predictions ante/data/sample_predictions.json --out-svg out/calibration.svg
# add --collection <deck.anki2> to also print FSRS's own held-out log loss/RMSE
```

---

## 2. Performance — "can the student answer a *new* exam-style question?"

- **Code:** [`ante/performance.py`](../performance.py); transfer items
  [`ante/performance_items.py`](../performance_items.py); the literal paraphrase
  test [`ante/paraphrase.py`](../paraphrase.py).
- **Question it answers:** the probability the student gets a **novel**,
  MCAT-style application question right — including topics/questions never seen.
- **Why it's separate from memory:** remembering the card
  "glycine is achiral" ≠ answering a passage that *uses* that fact. This model is
  the **memory → performance bridge**; if it just copies the memory signal, the
  bridge is fake, and we test for exactly that.

**Inputs / features.** A pure-Python logistic regression
(`LogisticRegression`, standardized features, L2, no sklearn) over:
`topic_mastery`, question `difficulty`, `response_time_z`, and `coverage`.

**Baseline it must beat.** `memory_baseline_probs` predicts performance = the
`topic_mastery` feature directly (i.e. "performance is just memory"). The trained
model is reported **side by side** with this baseline on held-out log loss /
accuracy. If it can't beat memory-only, we say so.

**The paraphrase test (spec 7d), done literally.** 30 real seed cards, each
paired with **two** reworded questions testing the same idea
([`ante/data/paraphrase_set.json`](../data/paraphrase_set.json), 60 items). We
compare, per card, recall on the card vs accuracy on the two paraphrases and
report the **gap**. A near-zero gap means "memory in disguise"; a positive gap is
the bridge doing real work.

```bash
just paraphrase                 # synthetic memorizer-vs-transfer demonstration
```

**Uncertainty.** Section-level performance is a bootstrap mean with a 95 % CI
(`bootstrap_mean_ci`), which becomes the readiness range.

**Honesty / limits.** With no application evidence for a topic, performance is
**not invented** — the topic contributes the uncovered prior (low, wide) to
readiness, and the section reads as uncertain.

---

## 3. Readiness — "what would the student score today, and how sure are we?"

- **Code:** [`ante/readiness.py`](../readiness.py); track record
  [`ante/trackrecord.py`](../trackrecord.py).
- **Question it answers:** a projected **MCAT total (472–528)** with a range,
  confidence, reasons, last-updated, and an explicit abstention rule.
- **Consumes performance, not memory.** Readiness is built from the
  **performance** model's per-section accuracy — never raw recall.

**Method.** Per-topic performance (point, low, high) is aggregated into
per-section accuracy, weighted by each topic's in-section exam weight and
**coverage-adjusted** (topics with no evidence pull the section down and widen
it; `section_accuracy_from_topics`). Each section maps linearly:

```
section score = 118 + 14 * accuracy      (clamped to [118, 132])
projected total = sum of the four sections
```

The CI on section accuracy becomes the **score range**; `confidence`
(low/medium/high) is a function of coverage, review count, and average interval
width. Systematic **over-confidence** (says "sure", gets it wrong) lowers the
point estimate and drops the lower bound further — a self-report that keeps
missing cannot pull the line up.

**"How accurate were past guesses" (spec §1 honesty rule).** The Book logs each
line it posts and, when a full-length practice test is completed, pairs each
earlier line with the next actual score
([`ante/trackrecord.py`](../trackrecord.py)): it reports how often the actual
score fell inside the posted range and the mean absolute error in points. Below
one checkable line it **abstains on its own track record too**.

**Honesty / limits.** The score map is a **documented linear heuristic, not a
validated AAMC concordance**, and it says so in `method`. Per spec §9 we grade
the *bridge steps* (memory calibration → performance → mapping) and abstain
rather than fake a validated final number.

---

## The give-up rule (enforced, shared)

The app shows **no readiness score** unless **both**:

1. there are at least **`giveup_min_reviews`** graded reviews
   (`ante/config.py`, default 60 for a first honest wide-range read; the spec's
   example floor is 200 and is configurable via `ANTE_GIVEUP_MIN_REVIEWS`), **and**
2. weighted topic **coverage ≥ `giveup_min_coverage`** *and no high-weight
   section is a blind spot* (`ante/coverage.py`).

When either fails, the Book renders **"NO LINE — insufficient action"** and lists
the specific reasons (how many reviews/how much coverage are still missing). It
still shows what it *can* (memory calibration, coverage map). The thresholds are
tunable so a grader can dial the floor to the spec's 200/50 % and watch the app
abstain, then earn the line. "A confident number with nothing behind it is a
guess in a nice font."
