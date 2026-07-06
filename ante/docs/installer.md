# Packaging & installers — desktop + phone (spec §2, §6, §12)

The spec requires **a desktop installer and a phone build that both run with AI
switched off**, on a clean machine/device. This documents how each is produced
and how to install it, plus the honest signing state of a local build.

Both apps run **with no API keys** — AI (card generation, tutor, generated
media) is provider-isolated and degrades to deterministic offline behavior, so
neither the installer nor the phone build depends on any AI service.

---

## Desktop installer

### Build

```bash
just installer          # → out/installer/dist/
```

This wraps the Briefcase pipeline (`./ninja installer`, i.e.
`qt/tools/build_installer.py`), which:

1. builds the `anki` + `aqt` wheels (release profile),
2. lays out a Briefcase app bundle for the host platform,
3. **bundles the `ante/` package** into the app's `app_packages/` via
   `bundle_ante()` — the den UI (`ante/web/`), the seed deck and outline
   (`ante/data/`), and all the pure-logic models — so the packaged app serves
   the den and scores **without the dev checkout present** (the online-only
   `ante/service` and tests are excluded), and
4. packages the platform installer into `out/installer/dist/`:
   - **macOS:** `Ante-<ver>-mac-<arch>.dmg`
   - **Windows:** `Ante-<ver>-win-<arch>.msi`
   - **Linux:** `Ante-<ver>-linux-<arch>.tar.zst`

### Install on a clean machine

- **macOS:** open the `.dmg`, drag the app to `/Applications`, launch. A local
  build is **adhoc-signed** (no Developer ID), so the first launch needs
  right-click → Open (or `xattr -dr com.apple.quarantine <App>`) to clear
  Gatekeeper. Signed & notarized builds (no prompt) come from the release
  workflow — see below.
- **Windows:** run the `.msi`. A local build is unsigned (SmartScreen "More
  info → Run anyway"); the release workflow signs the EXE + MSI.
- **Linux:** extract the `.tar.zst` and run the launcher inside; no system
  install required.

First launch self-seeds the full high-yield MCAT deck and opens the den — there
is nothing to import.

### Signed / notarized / store-ready builds

The GitHub release workflow (`.github/workflows/release.yml`, driven by
`just -f release.just …`) produces signed macOS (Developer ID + notarization),
signed Windows (Azure Trusted Signing, x64 + ARM), and Linux artifacts. Signing
requires the `release` environment secrets and is intentionally **not** part of
the local `just installer` path.

---

## Phone build (iOS)

The companion shares the **same modified Rust engine** as the desktop, compiled
for iOS as an xcframework (not a re-implementation).

### Build

```bash
just ios-engine         # cross-compile rslib → out/ios/AnkiEngine.xcframework
                        # (device arm64 + Apple-silicon simulator)
cd ios && xcodegen generate    # writes Ante.xcodeproj from project.yml
open Ante.xcodeproj            # then Product → Run (device or simulator)
```

`just ios-engine` verifies the Ante engine symbol (`ante_backend_run`) is present
in the static library before packaging the xcframework, so the phone provably
carries the same `points-at-stake` order + `GetTopicMastery` RPC as the desktop.

### Onto a device

- **Sideload (no paid account):** select your team for automatic signing in
  Xcode (bundle id `app.ante.ios`), pick your device, and Run. This is the
  clean-device path used for the demo.
- **TestFlight:** Archive → distribute to App Store Connect → TestFlight. This
  needs a paid Apple Developer account; the project is otherwise archive-ready
  (single target, no SPM dependencies, notifications requested at runtime).

### Engine-only smoke tests (no Xcode UI)

To prove the shared engine works end to end without the app, two host tests
build the engine and drive it through the exact call sequence the phone uses:

```bash
just ios-engine-smoke   # C caller: buildhash + open a collection + one RPC
just ios-swift-smoke    # the app's real Swift codec + FFI + SyncedEngine;
                        # with SYNC_* set, also downloads + answers + syncs back
```

---

## What "runs with AI off" means here

- No `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `HF_KEY` / `ELEVENLABS_API_KEY`:
  card generation falls back to the deterministic offline provider, the tutor
  and Back Room run typed/offline, and the Studio renders offline engraver
  plates. The three scores are computed by the in-process pure-Python models, so
  **readiness, mastery, and coverage all work fully offline** on both platforms.
