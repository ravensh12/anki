# Ante

**A members-only card den for the MCAT — an honest, mastery-gated study system, built as a fork of [Anki](https://apps.ankiweb.net).**

You open Ante seated in the Emerald Room, a card den above Canal Street, where
**Sahir** — a djinn who has dealt cards since Babylon — deals your due cards
onto green felt. Your opponent is the House: the forgetting curve. On top of
Anki's spacing engine (FSRS), Ante does three things Anki structurally cannot:

1. **Gates progress on demonstrated topic mastery** — tables on the Circuit
   are _won_ by application (quizzes + open-ended), not visited or self-rated.
2. **Measures the memory→performance gap** — can you use a fact on a novel
   MCAT-style question, not just recall the card?
3. **Posts a calibrated readiness line that abstains** — "NO LINE" beats a
   guess in a nice font.

The consumer promise is **time back**: the same retention from roughly half
the seat time, because the schedule — not extra hours — does the work.

## What's in this fork

| Path                | What it is                                                                                                     |
| ------------------- | -------------------------------------------------------------------------------------------------------------- |
| [`ante/`](ante/)    | The product: mastery engine, the den web UI (`web/den.html`), AI tutor/studio, seed data, tests, and docs      |
| [`ios/`](ios/)      | SwiftUI companion app — Tonight / Circuit / Table / Ledger, with native morning & midnight game notifications  |
| `rslib/`            | Engine changes: points-at-stake queue order + `GetTopicMastery` RPC (`rslib/src/scheduler/topics.rs`)          |
| `qt/aqt/ante*.py`   | Desktop integration: serves the den, auth, reminders, and the media studio inside the Anki app                 |
| everything else     | Upstream Anki (see below)                                                                                       |

> This work lives on the **`speedrun`** branch; `main` tracks upstream Anki.

## Quick start

Prereqs: `just`, a Rust toolchain, `n2` (`bash tools/install-n2`).

```bash
just run     # build + launch the desktop app
             # the den: http://localhost:40000/_anki/ante
```

Where to go next:

- [`ante/README.md`](ante/README.md) — the full system: the Circuit, the Book,
  the Back Room, the Studio, design principles, architecture, and every command.
- [`ios/README.md`](ios/README.md) — generate and run the iOS app
  (`xcodegen generate && open Ante.xcodeproj`).
- [`ante/docs/PRD.md`](ante/docs/PRD.md) — product requirements;
  [`ante/docs/learning-science.md`](ante/docs/learning-science.md) — the
  evidence map behind each feature.

AI features (card generation, the tutor, generated media) are optional and
provider-isolated — the app fully works with no API keys set.

## About Anki (upstream)

This repo is a fork of [ankitects/anki](https://github.com/ankitects/anki),
the computer version of Anki, a spaced repetition program by Ankitects Pty Ltd
and contributors. For upstream docs see [Development](./docs/development.md)
and the [Contribution Guidelines](./docs/contributing.md).

## License

GNU AGPL-3.0-or-later, inheriting [Anki's license](./LICENSE). Some Anki
components are BSD-3-Clause.
