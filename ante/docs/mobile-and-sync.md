# Mobile companion + shared engine + sync (PRD 3.1, 5.3, 7b, B5/B6)

This documents how Ante's **desktop and iOS apps share one Rust engine**, and
how two-way sync is proven. It is the mobile half of the Rust-change note.

The v1 companion is **iOS (SwiftUI)**. The shared-engine approach was first
validated on Android (see the migration note at the end); the iOS app reuses the
identical engine change and the same platform-neutral sync proof.

## Two apps, one engine

```
[Desktop: Qt/Python UI] ─┐
                         ├─► [shared anki Rust backend, MODIFIED] ─► SQLite collection
[iOS: SwiftUI UI] ───────┘        (points-at-stake queue + GetTopicMastery RPC)
        │  (sync, both ways)
        └──────────────────────────► [self-hosted Anki sync server]
```

The engine change lives in the `anki` crate (`rslib`) so it ships to **both**
platforms unchanged:

- Desktop consumes it directly (Python `_backend` over protobuf).
- iOS consumes it as an **xcframework** built from the same crate, called over the
  single existing protobuf seam `Backend::run_service_method(service, method,
  bytes)` — a thin C FFI (or a UniFFI layer) is the only bridge.

This is a real shared engine — not a Swift/JS re-implementation. The same
`REVIEW_CARD_ORDER_POINTS_AT_STAKE` order and the same `GetTopicMastery` RPC run
on the phone and the desktop.

## How the engine gets onto the phone

1. **Cross-compile `rslib`** (the modified `anki` crate) for the Apple targets:

   ```
   rustup target add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios
   cargo build -p anki --release --target aarch64-apple-ios       # device
   cargo build -p anki --release --target aarch64-apple-ios-sim   # Apple-silicon sim
   ```

   The engine change is entirely in the crate, so nothing platform-specific is
   ported — the same source that builds the desktop backend builds the iOS libs:
   - `proto/anki/scheduler.proto` — `GetTopicMastery` RPC + messages.
   - `proto/anki/deck_config.proto` — `REVIEW_CARD_ORDER_POINTS_AT_STAKE = 13`.
   - `rslib/src/scheduler/topics.rs` — topic weighting + mastery rollup.
   - `rslib/src/scheduler/queue/builder/mod.rs`, `service/mod.rs`, `mod.rs` —
     wire the queue order + RPC.
   - `rslib/src/storage/card/mod.rs`, `storage/sqlite.rs` — the `points_at_stake`
     SQL function and `topic_mastery_rows`.

2. **Package an xcframework** from the resulting static libraries
   (`xcodebuild -create-xcframework`), exposing the single C-ABI entry point that
   wraps `run_service_method(service, method, input_bytes) → output_bytes`. Verify
   the engine is really inside the binary:

   ```
   nm -gU libanki.a | grep run_service_method            # symbol present
   ```

3. **Bridge into Swift**: a bridging header (or the UniFFI-generated module) plus
   the protobuf message types generated for Swift, so requests/responses encode
   and decode across the FFI boundary.

4. **Wire `SyncedEngine`** (in `ios/Ante/EngineClient.swift`) to that entry
   point via the `AnkiServiceBridge` protocol. No UI changes: `fetchMastery()`
   calls `GetTopicMastery`, `fetchDue()` calls `GetQueuedCards`, `fetchScores()`
   derives the three scores the way the desktop `ante` layer does.

The engine already carries the portability fix the debug (`.so`) build needed and
which applies equally to overflow-checked iOS builds: the FSRS retrievability
day→second conversion uses `saturating_mul` so a very-overdue card cannot overflow
`u32` and panic, and `topic_mastery_rows` computes retrievability in Rust behind a
panic guard rather than via a SQL scalar function, so one odd card can never unwind
across the FFI boundary.

## Three scores on the phone

The iOS **Today** and **Atlas** surfaces call the shared engine's
`getTopicMastery("", "mcat::", 0.9)` and render memory / performance / readiness
(each with a range) plus the mastery map, in the Ante editorial aesthetic.
Readiness is built on **application** evidence (transfer items answered in the
desktop Quiz, synced across), so the phone **abstains** until that evidence
exists — honest by construction. When the live engine is unavailable and the app
is serving `MockEngine` data, the surfaces carry a visible **SAMPLE DATA** badge
so a mocked, abstaining reading is never mistaken for a real one.

## Why iOS is the right companion for _this_ product

Ante's core ritual is the daily bookend: retrieval in the morning, a light
review right before sleep. That ritual only works if the cue is **reliable**.

- On iOS, the windows are scheduled as repeating `UNCalendarNotificationTrigger`
  local notifications through **UNUserNotificationCenter**. They fire at the
  student's morning / midday / pre-sleep windows **even with the app closed** —
  no background execution, no server round-trip, no daemon.
- They are **quiet-hours aware**: any window inside the protected sleep window is
  dropped before scheduling (the same rule as `ante/reminders.py`), and the
  copy is the same no-shame, cue-anchored text ("start cold, the top of the stack
  is already chosen"; "a few cards before bed … your brain consolidates them
  while you sleep").
- The desktop needs a launch agent (launchd) to nudge when the app isn't running;
  the phone gets first-class scheduled delivery natively. The companion that lives
  in your pocket is the right home for a time-of-day ritual.

Permission is requested at runtime (no Info.plist key required), the schedule
re-arms whenever the profile changes, and it is cancelled entirely when reminders
are turned off. The Plan tab previews exactly what is armed on the device.

## Sync (PRD 7b / 11)

- **Server:** Anki's built-in sync server, self-hosted (`just sync-server`).
- The iOS app points its sync URL at that server and logs in; a full session
  (2,884 notes / 2,887 cards + media) uploads/downloads through the shared engine
  (the same media/collection sync the crate implements — no separate mobile sync
  code).
- **Re-runnable sync test** (`just sync-test` → `ante/tools/sync_test.py`).
  This tool is **platform-neutral** — it drives two collections through the same
  engine and sync server that the phone uses, so it proves the sync contract the
  iOS client depends on:
  1. 10 offline reviews on "desktop" + 10 different on "phone" → after sync, all
     20 are present exactly once on both sides (none lost, none doubled).
  2. The same card reviewed offline on both sides → sync converges on a single,
     documented winner (last sync wins); collections do not diverge.
  3. Integrity check passes on both collections afterward.

## Status (honest)

- **Done — the shared engine is live on the phone.** The app defaults to
  `SyncedEngine` (`ios/Ante/AnteApp.swift`), which runs the **real** modified
  `anki` Rust crate on-device:
  1. `just ios-engine` cross-compiles `rslib` for `aarch64-apple-ios` + `-sim`
     and packages `out/ios/AnkiEngine.xcframework`, asserting the Ante symbol
     (`ante_backend_run`) is in the binary.
  2. The C entry point is reached through a hand-rolled protobuf codec
     (`ios/Ante/Engine/ProtoWire.swift`, `BackendMessages.swift`,
     `Generated/BackendIndices.swift`) over the single
     `run_service_method`-style seam — no per-RPC native glue, no rewrite.
  3. `SyncedEngine` opens the collection, calls `GetTopicMastery`/`GetQueuedCards`,
     answers cards, and derives the three scores the way the desktop `ante`
     layer does. It **abstains** on readiness until synced application evidence
     exists.
  4. **Two-way sync is wired**: `connect()` → `syncLogin`; `sync()` runs the
     normal `sync_collection`, and handles `FULL_DOWNLOAD`/`FULL_UPLOAD`/
     `FULL_SYNC` against the self-hosted server, reopening the collection after a
     full transfer — the exact sequence `ante/tools/sync_test.py` proves.
- **Fallback (honest):** if the engine can't start, `SyncedEngine` falls back to
  `MockEngine` and `liveStatus()` returns `nil`; the UI then shows a visible
  **SAMPLE DATA** badge (see `AppModel.usingSampleData`) so mocked numbers are
  never mistaken for real ones.
- **Verify without Xcode:** `just ios-engine-smoke` (C caller) and
  `just ios-swift-smoke` (the app's real Swift codec + FFI + `SyncedEngine`,
  optionally full-download + answer + sync-back) exercise the production path
  against the host-built engine.
- **Next (packaging, not engine):** a paid Apple account for TestFlight; the
  sideload build is the clean-device path today.

## Migration note — Android companion (superseded)

The v1 companion strategy was a **fork of AnkiDroid**; it is superseded by the
iOS app above, but it already validated the shared-engine approach on one mobile
platform:

- The modified `anki` crate was cross-compiled with `cargo-ndk` into
  **`librsdroid.so`** (verified to contain `add_points_at_stake_function` + the
  `mcat::` topic logic), published as an `.aar`, and consumed by the AnkiDroid fork.
- A "Ante scores" screen called the shared engine's `getTopicMastery` and
  rendered the three ranged scores + mastery map, abstaining honestly.
- The re-runnable sync test passed end-to-end against the self-hosted sync server.

So the "same Rust engine on the phone, over the single protobuf seam, with
two-way sync" claim is not speculative — it shipped once already; iOS reuses the
identical engine change and the same platform-neutral sync proof.

## Files touched (mobile)

- `ios/` (this repo): the SwiftUI scaffold — `project.yml` (XcodeGen),
  `Ante/` sources (app, theme, models, engine client, app model, notification
  scheduler, and the five screens), and `ios/README.md`.
- This repo (shared, platform-neutral): `ante/tools/sync_test.py`, `justfile`
  (`sync-server`, `sync-test`), and the engine change in `rslib` / `proto`.
- Superseded (not in this repo): the `Anki-Android` / `Anki-Android-Backend`
  forks from the Android write-up.
