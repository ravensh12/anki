# Notifications — the morning/night ritual, on every platform

Ante's consumer promise ("same learning, half the time") rests on a _when-to-study_ ritual: a short retrieval session before the day starts and a light review before sleep. A ritual is only real if the cue arrives **reliably**, including when the app is closed. This is how each platform delivers it.

## The two bookends (the product ritual)

The day is bracketed by two nudges, each grounded in the most-replicated learning science:

| Bookend         | Time                           | Science                                                                                                                                     |
| --------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **First Light** | morning window (default 08:00) | Retrieval practice; cold recall beats warm rereading (Roediger & Karpicke 2006). Firing _before coffee_ removes the decision (Principle 2). |
| **Last Light**  | night window (default 21:00)   | A light pre-sleep review the brain consolidates overnight — the cheapest minutes of the day.                                                |

The exact times come from the recalibrated day-plan (`ante/recalibrate.py::slot_plan`), placed by chronotype, and are always suppressed inside quiet hours (`profile.in_quiet_hours`). The pure schedule + copy live in `ante/reminders.py`; the ritual's _state_ (done / next / no-shame headline) lives in `ante/ritual.py` and drives the Today screen's bookends strip.

## Desktop — two layers

Desktop has a real gap: an in-app timer can only nudge while Anki is open, which defeats a morning cue. Ante closes it with two layers.

### 1. In-app (app is open) — `qt/aqt/ante_reminders.py`

A one-minute `QTimer` checks the day's schedule and, when a window arrives, fires a **native banner**:

- **macOS** — a real Notification Center banner via `osascript` (`display notification … with title … sound name "Glass"`), not just a tray balloon.
- **Windows / Linux** — the Qt system-tray notification, falling back to an in-app toast.

Settings → _Send a test notification_ fires one immediately (using the next scheduled reminder's real copy) so the student sees exactly what a nudge looks like.

### 2. OS-scheduled (app is closed) — `ante/os_notify.py`

Opt-in ("Fire even when Ante is closed"). On save, Ante registers the day's reminder times with the operating system's own scheduler, so the nudge arrives like an alarm clock even if Anki never launched that morning:

- **macOS** — per-reminder **launchd** user agents in `~/Library/LaunchAgents` (`app.ante.reminder.*`) whose `StartCalendarInterval` runs `osascript` to post a banner.
- **Windows** — daily **Task Scheduler** jobs (`schtasks`, folder `Ante`) running a generated PowerShell toast script.
- **Linux** — **systemd** user timers (`ante-reminder-*.timer`) running `notify-send`.

Design rules, all enforced and tested (`ante/tests/test_os_notify.py`):

- **Reversible.** `uninstall_all()` removes every artifact; turning the switch off, or turning reminders off, wipes the jobs. Toggling reminders in Settings re-syncs via `qt/aqt/ante.py::sync_os_reminders`, and the in-app scheduler re-syncs once a day.
- **Quiet-hours safe.** A window suppressed by quiet hours never produces a job (the schedule it reads is already filtered).
- **Evergreen copy.** OS jobs can't know today's live due-count, so they carry the cue ("recall before coffee", "a few cards before bed") without a fabricated number.
- **Never fatal.** Every scheduler call is wrapped; a failed nudge can't take the app down.

## iOS — first-class by construction

On the phone the ritual is native. `NotificationScheduler.swift` (see `ios/`) requests `UNUserNotificationCenter` authorization once and schedules repeating `UNCalendarNotificationTrigger` local notifications for each window, with the same no-shame copy and quiet-hours logic. These fire whether or not the app is running — which is exactly why iOS is the right mobile companion for a _when-to-study_ product (see `mobile-and-sync.md`).

## Honesty note

Reminders are opt-in (autonomy is part of Principle 4) and never shaming. Missing a bookend produces "no shame — tonight's Last Light still counts", never a broken-streak scold. The streak that a bookend feeds is **effort-gated**: a day counts only when genuine study happened, not when a notification was delivered or the app was opened (`ante/rewards.py::day_counts`).
