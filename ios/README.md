# Ante — iOS companion

The mobile half of Ante: a SwiftUI app that mirrors the desktop den (an honest,
mastery-gated MCAT study system played as a nightly card game, forked on Anki)
and makes the Morning Game / Midnight Game ritual first-class through reliable
local notifications. It shares the **same modified `anki` Rust engine** as the
desktop — the same stakes-ordered queue and the `GetTopicMastery` RPC —
consumed on-device as an xcframework (see
[Shared engine](#shared-engine-the-next-step)).

This directory is a complete, buildable scaffold: the full UI, onboarding,
notification scheduler, and a mock engine are implemented today; the Rust
xcframework build and sync wiring are the documented next step.

## The four surfaces

| Tab         | What it is                                                                                                            |
| ----------- | --------------------------------------------------------------------------------------------------------------------- |
| **Tonight** | The countdown to the final table, one CTA (take your seat), the Book's line (or its honest abstention), the two games, and the 30-night run. |
| **Circuit** | The world tour: New York's Emerald Room, Monte Carlo's Salon Bleu, Havana's Casa Verde, Macau's Jade House — topic tables with won / open / low-table / roped-off states. |
| **Table**   | The dealt game: cream card faces on dark felt, Check/Call/Raise before every flip, Again/Hard/Good/Easy after, an application hand every fourth step. |
| **Ledger**  | The plan recalibrated to the exam date: pacing, shape of the day, the day's calls, and the levers (exam date, target score, quiet hours, the run). |

## Requirements

- macOS with **Xcode 15+** (iOS 17 SDK).
- **XcodeGen** to generate the project from `project.yml`:

```bash
brew install xcodegen
```

- **No third-party packages.** The app uses only SwiftUI, Foundation, UIKit
  (for color literals), and UserNotifications. There is no SPM manifest and
  nothing to resolve.

## Generate and run

```bash
cd ios
xcodegen generate          # writes Ante.xcodeproj from project.yml
open Ante.xcodeproj
```

Then, in Xcode: pick an iOS 17+ simulator (or a device) and press Run. On a
device you'll be asked to select your team for automatic signing; the bundle id
is `app.ante.ios`.

- **Deployment target:** iOS 17.0.
- **First launch:** the date-first onboarding runs; finish it and the four tabs
  appear. Everything is driven by the bundled `MockEngine`, so it works fully
  offline with no backend.
- **Notifications:** onboarding requests permission if game calls are enabled;
  the schedule is armed immediately and previewed on the Ledger tab. To see one
  fire quickly, set your quiet hours to a narrow window and a study window to
  the current hour, then recalibrate.

## Architecture

A thin, one-way data flow: SwiftUI views observe a single `AppModel`, which
reads from an `EngineClient` and persists the durable profile. The engine
boundary is the only place that knows whether data is mocked or coming from the
real Rust core, so the UI never changes when the engine is swapped in.

```
┌──────────────────────── SwiftUI views ───────────────────────┐
│ Onboarding · Tonight · Circuit · Table · Ledger               │
└───────────────▲───────────────────────────────┬──────────────┘
                │ @EnvironmentObject             │ intents
                │                                ▼
         ┌──────┴───────────────────────────────────────┐
         │ AppModel (ObservableObject, @MainActor)       │
         │  · StudyProfile  (UserDefaults + Codable)     │
         │  · scores / mastery / due  (from the engine)  │
         │  · RitualState   (the two games + the run)    │
         │  · RecalibrationPlan + call schedule          │
         └───────┬───────────────────────────┬──────────┘
                 │                            │
     ┌───────────▼──────────┐    ┌────────────▼───────────────┐
     │ EngineClient         │    │ NotificationScheduler      │
     │  · MockEngine (now)  │    │  UNUserNotificationCenter  │
     │  · SyncedEngine ─────┼──▶ │  (UNCalendarNotification-  │
     │     AnkiServiceBridge│    │   Trigger, quiet-hours      │
     │     → run_service_method   │   aware, repeats daily)    │
     └──────────────────────┘    └────────────────────────────┘
```

### Files (`Ante/`)

| File                          | Responsibility                                                                                                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AnteApp.swift`               | `@main` App; injects `AppModel`; shows onboarding until `profile.onboarded`, then the tabs. Forces the den's dark scheme.                                                   |
| `Theme.swift`                 | The Ante palette (felt/panel/ink/brass/ember/card, mirroring `ante/web/den.html`), spacing, serif/mono type modifiers, and shared chrome (`TickBar`, `Wordmark`, `SectionHeader`, `AnCTAButton`, `AnSegmented`, `AnMeter`, `ChipStack`, panels). |
| `Models.swift`                | Pure-Foundation data + logic: `StudyProfile`, `ScoresSnapshot`, `TopicMastery`, `RitualState`, the Circuit's city/table vocabulary, session items, and the `RecalibrationPlan` / `ReminderBuilder` logic mirrored from the desktop. |
| `EngineClient.swift`          | The `EngineClient` protocol, `MockEngine` (realistic sample data, the Book abstains), and `SyncedEngine` (the typed skeleton for the real Rust core over `run_service_method`). |
| `AppModel.swift`              | `ObservableObject` store: persists the profile, fetches from the engine, tracks the two games and the run, keeps calls in sync, and owns tab routing.                       |
| `NotificationScheduler.swift` | Requests authorization and turns game calls into repeating calendar notifications; reschedules on change, cancels when off, and can read back what's armed.                 |
| `OnboardingView.swift`        | The date-first, three-step flow plus the "Recalibrating…" interstitial that ends at the Emerald Room.                                                                       |
| `MainTabView.swift`           | The four surfaces and the shared den page scaffold (`AnScreen`).                                                                                                            |
| `TodayView.swift`             | Tonight: countdown, one CTA, the Book's line (or the abstention stamp), the Morning/Midnight Games, and the 30-night run tracker.                                           |
| `AtlasView.swift`             | The Circuit: city-by-city table map with states, chip stakes, percentages, and confidence bands.                                                                            |
| `SessionView.swift`           | The Table: pre-flip Check/Call/Raise, Again/Hard/Good/Easy on cream card faces, and an application hand every fourth step with a confidence lock-in.                        |
| `PlanView.swift`              | The Ledger: the recalibration summary, shape-of-the-day slots, the call preview, and inline exam/target editing.                                                            |

### Design system

One source of truth in `Theme.swift`, mirroring `ante/web/den.html`: deep felt
green ground (`#0c1712`), brass accents (`#c9a227`), ember warnings
(`#b5533c`), cream card faces (`#f6efdd`), flat 1px rules, near-square corners,
a serif display face (New York via `design: .serif`), and monospaced uppercase
micro-labels. The app is always night — the den has no daytime. The tone is an
honest house, never a cheerleader.

### Notifications: why iOS

The game calls are scheduled as repeating `UNCalendarNotificationTrigger` local
notifications, so they fire at the student's windows **even with the app
closed** — no background mode, no server. The copy is the same no-shame,
cue-anchored text as `ante/reminders.py` ("The morning game opens", "Last hand
before lights out"), and any window inside quiet hours is dropped before
scheduling. On the desktop this needs a launch agent; on the phone it is
native.

## Shared engine: the next step

`SyncedEngine` is a typed skeleton. To make it live, compile the modified
`anki` crate (the Ante engine change from `rslib`) for iOS and drop it in
behind the `AnkiServiceBridge` protocol:

1. **Build the static libs** for device and simulator:

   ```bash
   rustup target add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios
   cargo build -p anki --release --target aarch64-apple-ios
   cargo build -p anki --release --target aarch64-apple-ios-sim
   ```

2. **Package an xcframework** from the resulting `libanki` static libraries
   (`xcodebuild -create-xcframework -library … -headers …`), exposing the single
   C-ABI entry point `run_service_method(service, method, input_bytes) →
   output_bytes` — the same seam the desktop uses. A thin C shim (or a
   UniFFI-generated Swift module) is the bridging layer.

3. **Add a bridging header** (or import the UniFFI module) so Swift can call the
   entry point, and generate the protobuf message types for Swift so requests and
   responses can be encoded/decoded (`GetTopicMasteryRequest`, the queued-cards
   messages, etc.).

4. **Implement `AnkiServiceBridge`** over that entry point and pass it to
   `SyncedEngine(bridge:)`. Replace the mock fallbacks: `fetchMastery()` calls
   `SchedulerService.GetTopicMastery`, `fetchDue()` uses `GetQueuedCards`, and
   `fetchScores()` derives the three scores the way the desktop `ante` layer
   does. Add two-way sync against the self-hosted Anki sync server around
   sessions so application evidence from the desktop reaches the Book here.

Nothing in the UI or `AppModel` changes when this lands — only the engine behind
the protocol. See `../ante/docs/mobile-and-sync.md` for the full engine + sync
story and the honest status of this build.

## License

GNU AGPL-3.0-or-later, inheriting Anki's license. Credit: Anki by Ankitects Pty
Ltd and contributors. Some Anki components are BSD-3-Clause.
