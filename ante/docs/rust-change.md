# Ante Rust engine change

This is the required brownfield change to Anki's Rust core. It adds two
capabilities Anki structurally lacks, both of which belong in the engine rather
than in a Python add-on.

## What changed

1. **Points-at-stake review order** (`REVIEW_CARD_ORDER_POINTS_AT_STAKE`). A new
   review-queue ordering that sorts due cards by **topic exam-weight x student
   weakness**, highest first. Weakness is `1 - FSRS retrievability`; topic weight
   comes from each card's note tags (`mcat::<section>::...`). Cards with no FSRS
   memory state are treated as maximally weak, so the order degrades gracefully
   to "highest-yield topic first" when FSRS is off.

2. **`GetTopicMastery` RPC**. A backend call that returns, per topic, the total
   cards, studied cards, mastered cards (retrievability >= threshold), average
   recall, and coverage. It powers the mastery/coverage dashboard and the
   coverage-based abstention rule.

The topic-weight logic lives in one place
([`rslib/src/scheduler/topics.rs`](../../rslib/src/scheduler/topics.rs)) and is
shared by both the SQL ordering function and the mastery query.

## Why this belongs in Rust, not Python

- **Correctness under limits.** The review queue applies per-deck limits _during_
  the gather, stopping once the limit is hit. To pick the _right_ highest-value
  cards under a limit, the ordering must happen in the gather query itself
  (`review_order_sql` -> a registered SQLite function), not as a post-hoc Python
  re-sort of an already-truncated list. A Python add-on can only reorder what the
  engine already chose, which is the wrong set.
- **Speed on 50k cards.** Both features run inside SQLite over the cards table in
  a single pass (the mastery rollup is one query plus an O(n) aggregation), so
  they stay within the dashboard/queue latency targets. Round-tripping every
  card's FSRS state to Python would not.
- **Shared with mobile.** Anki's engine is the single shared core
  (`Backend::run_service_method`). Putting the change in Rust means it ships to
  the desktop and any future phone client automatically; a Python/Qt-only change
  would not exist on mobile.
- **Native primitive.** "Review order" and backend RPCs are first-class engine
  concepts. Extending the `ReviewCardOrder` enum and the `SchedulerService` is
  the idiomatic seam, reusing FSRS retrievability the engine already computes.

## Files touched

### Upstream files modified (merge risk noted)

- `proto/anki/deck_config.proto` - one new enum value (additive; trivial merge).
- `proto/anki/scheduler.proto` - one new RPC + 3 messages (additive; trivial).
- `rslib/src/storage/sqlite.rs` - register `points_at_stake` SQLite function +
  shared retrievability helper (additive; low risk).
- `rslib/src/storage/card/mod.rs` - new `ReviewOrderSubclause` variant, its SQL,
  and the `topic_mastery_rows` query (additive; low risk).
- `rslib/src/scheduler/mod.rs` - declare the `topics` module (1 line; trivial).
- `rslib/src/scheduler/service/mod.rs` - wire the `get_topic_mastery` trait
  method (additive; trivial).
- `rslib/src/scheduler/queue/builder/mod.rs` - new ordering unit test (test-only).
- `rslib/src/scheduler/fsrs/simulator.rs` - add the new enum arm to an exhaustive
  match (1 line; the only change forced by exhaustiveness, low risk).
- `ts/routes/deck-options/choices.ts` - expose the order in the deck-options UI
  (additive; trivial).
- `ftl/core/deck-config.ftl` - one new translation string (additive; trivial).

### New files (no merge risk)

- `rslib/src/scheduler/topics.rs` - topic-weight + mastery rollup logic + tests.
- `pylib/tests/test_ante.py` - Python integration tests.

**Overall merge difficulty: low.** Every upstream edit is additive (new enum
value, new RPC, new SQL function, new module). The only change forced by Rust's
exhaustiveness checking is a single match arm in `simulator.rs`. No upstream
behaviour is altered, so a future rebase onto Anki should apply cleanly.

## Tests

- Rust unit tests (`cargo`/`just test-rust`):
  - `topics::test::topic_extraction_filters_by_prefix`
  - `topics::test::section_weights_are_applied`
  - `topics::test::card_weight_takes_the_maximum_topic`
  - `topics::test::topic_mastery_groups_and_weights`
  - `scheduler::queue::builder::test::points_at_stake_orders_by_topic_weight`
- Python integration tests (`pylib/tests/test_ante.py`):
  - `test_get_topic_mastery_groups_by_tag` - calls the new RPC end to end.
  - `test_points_at_stake_review_order` - drives the new order via `col.sched`.
  - `test_points_at_stake_undo_and_no_corruption` - proves undo still works and
    `check_database` reports no problems after answering in the new order.

## Undo / corruption safety

The mastery query is read-only. The points-at-stake order only changes the
_presentation order_ of due cards; it does not mutate cards, so the existing
answer/undo machinery is untouched. `test_points_at_stake_undo_and_no_corruption`
confirms a card's state is restored by `col.undo()` and that `fix_integrity()`
(i.e. `check_database`) finds no problems.
