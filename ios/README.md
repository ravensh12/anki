# Ante — iOS companion

The mobile half of Ante: a SwiftUI app that mirrors the desktop den (an honest,
mastery-gated MCAT study system played as a nightly card game, forked on Anki)
and makes the Morning Game / Midnight Game ritual first-class through reliable
local notifications. It shares the **same modified `anki` Rust engine** as the
desktop — the same stakes-ordered queue and the `GetTopicMastery` RPC —
consumed on-device as an xcframework (see
[Shared engine](#shared-engine-the-next-step)).

This is a complete, buildable app: the full UI, onboarding, notification
scheduler, **and the live shared Rust engine** (`SyncedEngine`, the default)
with two-way sync are implemented. A `MockEngine` remains only as a labelled
fallback for previews and for when the engine can't start — the UI shows a
visible **SAMPLE DATA** badge whenever mocked data is on screen.

## The four surfaces

| Tab         | What it is                                                                                                                                                                |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Tonight** | The countdown to the final table, one CTA (take your seat), the Book's line (or its honest abstention), the two games, and the 30-night run.                              |
| **Circuit** | The world tour: New York's Emerald Room, Monte Carlo's Salon Bleu, Havana's Casa Verde, Macau's Jade House — topic tables with won / open / low-table / roped-off states. |
| **Table**   | The dealt game: cream card faces on dark felt, Check/Call/Raise before every flip, Again/Hard/Good/Easy after, an application hand every fourth step.                     |
| **Ledger**  | The plan recalibrated to the exam date: pacing, shape of the day, the day's calls, and the levers (exam date, target score, quiet hours, the run).                        |

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

| File                          | Responsibility                                                                                                                                                                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AnteApp.swift`               | `@main` App; injects `AppModel`; shows onboarding until `profile.onboarded`, then the tabs. Forces the den's dark scheme.                                                                                                                        |
| `Theme.swift`                 | The Ante palette (felt/panel/ink/brass/ember/card, mirroring `ante/web/den.html`), spacing, serif/mono type modifiers, and shared chrome (`TickBar`, `Wordmark`, `SectionHeader`, `AnCTAButton`, `AnSegmented`, `AnMeter`, `ChipStack`, panels). |
| `Models.swift`                | Pure-Foundation data + logic: `StudyProfile`, `ScoresSnapshot`, `TopicMastery`, `RitualState`, the Circuit's city/table vocabulary, session items, and the `RecalibrationPlan` / `ReminderBuilder` logic mirrored from the desktop.              |
| `EngineClient.swift`          | The `EngineClient` protocol, `MockEngine` (realistic sample data, the Book abstains), and `SyncedEngine` (the typed skeleton for the real Rust core over `run_service_method`).                                                                  |
| `AppModel.swift`              | `ObservableObject` store: persists the profile, fetches from the engine, tracks the two games and the run, keeps calls in sync, and owns tab routing.                                                                                            |
| `NotificationScheduler.swift` | Requests authorization and turns game calls into repeating calendar notifications; reschedules on change, cancels when off, and can read back what's armed.                                                                                      |
| `OnboardingView.swift`        | The date-first, three-step flow plus the "Recalibrating…" interstitial that ends at the Emerald Room.                                                                                                                                            |
| `MainTabView.swift`           | The four surfaces and the shared den page scaffold (`AnScreen`).                                                                                                                                                                                 |
| `TodayView.swift`             | Tonight: countdown, one CTA, the Book's line (or the abstention stamp), the Morning/Midnight Games, and the 30-night run tracker.                                                                                                                |
| `AtlasView.swift`             | The Circuit: city-by-city table map with states, chip stakes, percentages, and confidence bands.                                                                                                                                                 |
| `SessionView.swift`           | The Table: pre-flip Check/Call/Raise, Again/Hard/Good/Easy on cream card faces, and an application hand every fourth step with a confidence lock-in.                                                                                             |
| `PlanView.swift`              | The Ledger: the recalibration summary, shape-of-the-day slots, the call preview, and inline exam/target editing.                                                                                                                                 |

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

## Shared engine (live)

`SyncedEngine` runs the **real** modified `anki` Rust crate on-device — the same
`points-at-stake` order and `GetTopicMastery` RPC as the desktop. Build it once:

```bash
just ios-engine        # cross-compiles rslib → out/ios/AnkiEngine.xcframework
                       # (device arm64 + Apple-silicon sim); asserts the Ante
                       # symbol ante_backend_run is present in the binary
```

Then `xcodegen generate && open Ante.xcodeproj` links the xcframework
(`project.yml`). How the pieces fit:

1. **Bridge:** `Ante/Engine/AnteBridging.h` → `rslib/ios-ffi/include/anki_engine.h`
   exposes the single C entry point; `Ante/Engine/AnkiEngine.swift` wraps it.
2. **Codec:** a hand-rolled protobuf encoder/decoder
   (`Ante/Engine/ProtoWire.swift`, `BackendMessages.swift`,
   `Generated/BackendIndices.swift`) encodes requests / decodes responses over
   the one `run_service_method`-style seam — no per-RPC native glue.
3. **Client:** `SyncedEngine` opens the collection, calls
   `GetTopicMastery`/`GetQueuedCards`, answers cards, derives the three scores,
   and does two-way sync (`syncLogin` → `sync_collection` →
   full up/download as required) against the self-hosted server.

`MockEngine` is used only when the engine can't start; the UI then shows a
visible **SAMPLE DATA** badge (`AppModel.usingSampleData`) so mocked, abstaining
numbers are never mistaken for real ones. Verify the whole path without the Xcode
UI via `just ios-engine-smoke` and `just ios-swift-smoke`. See
`../ante/docs/mobile-and-sync.md` for the full engine + sync story.

## License

GNU AGPL-3.0-or-later, inheriting Anki's license. Credit: Anki by Ankitects Pty
Ltd and contributors. Some Anki components are BSD-3-Clause.
