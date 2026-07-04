# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ante study reminders — desktop delivery for the when-to-study schedule.

The pure schedule (times + learning-science copy) is computed in
``ante.reminders``; this module just delivers it: a lightweight QTimer wakes
once a minute, and when a scheduled window arrives it fires a native
notification — on macOS a real Notification Center banner (via ``osascript``,
with a soft sound), elsewhere the system tray, falling back to an in-app toast.
Reminders are cue-anchored, no-shame, and suppressed inside quiet hours, so the
app nudges the next correct action without nagging (Principle 2).

Delivery while the app is CLOSED is handled separately by
``ante.os_notify`` (launchd / Task Scheduler / systemd), toggled from
Settings ("even when Ante is closed").
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime
from typing import Any

_scheduler: _Scheduler | None = None


def _macos_banner(title: str, body: str) -> bool:
    """Post a native Notification Center banner on macOS. Returns success."""
    if sys.platform != "darwin":
        return False
    try:
        from ante.os_notify import osascript_notification

        script = osascript_notification(title, body)
    except Exception:
        safe_t = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_b = body.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'display notification "{safe_b}" with title "{safe_t}" sound name "Glass"'
        )
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


class _Scheduler:
    def __init__(self, mw) -> None:
        self.mw = mw
        self.tray: Any = None
        self.timer: Any = None
        self._schedule: list[dict] = []
        self._fired: set[str] = set()
        self._day: date | None = None
        self._scheduled_with_col = False

    def start(self) -> None:
        self._ensure_tray()
        from aqt.qt import QTimer

        self.timer = QTimer(self.mw)
        self.timer.setInterval(60_000)  # check every minute
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._tick()

    def _ensure_tray(self) -> None:
        try:
            from aqt.qt import QSystemTrayIcon

            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            self.tray = QSystemTrayIcon(self.mw.windowIcon(), self.mw)
            self.tray.setToolTip("Ante")
            self.tray.show()
        except Exception:
            self.tray = None

    def reschedule(self) -> None:
        self._fired = set()
        self._day = datetime.now().date()
        col = getattr(self.mw, "col", None)
        self._scheduled_with_col = bool(col)
        if not col:
            self._schedule = []
            return
        try:
            from aqt.ante import build_reminder_schedule

            self._schedule = build_reminder_schedule(col)
        except Exception:
            self._schedule = []
        # once a day, keep the OS-level (app-closed) jobs in step with the plan
        try:
            from aqt.ante import sync_os_reminders

            sync_os_reminders(col)
        except Exception:
            pass

    def _tick(self) -> None:
        col = getattr(self.mw, "col", None)
        now = datetime.now()
        if self._day != now.date() or (col and not self._scheduled_with_col):
            self.reschedule()
        if not col or not self._schedule:
            return
        cur = now.hour * 60 + now.minute
        for r in self._schedule:
            at = r.get("at")
            if not at or at in self._fired:
                continue
            rmin = int(r.get("hour", 0)) * 60 + int(r.get("minute", 0))
            # fire within a 5-minute window of the scheduled time
            if 0 <= cur - rmin <= 5:
                self._fire(r.get("title", "Ante"), r.get("body", "The table's open."))
                self._fired.add(at)

    def _fire(self, title: str, body: str) -> None:
        # Best-effort native banner (macOS may silently suppress osascript
        # notifications until the user allows them in System Settings), PLUS an
        # unconditional in-app nudge card so a cue is never invisible.
        banner = _macos_banner(title, body)
        if not banner and self.tray is not None:
            try:
                from aqt.qt import QSystemTrayIcon

                self.tray.showMessage(
                    title, body, QSystemTrayIcon.MessageIcon.Information, 12_000
                )
            except Exception:
                pass
        _in_app_nudge(self.mw, title, body)


def _in_app_nudge(mw, title: str, body: str) -> None:
    """Render the nudge inside the den (a styled slide-in card), falling back
    to a plain Qt tooltip if the webview can't take it."""
    import json as _json

    try:
        mw.web.eval(
            f"window.anNudge && anNudge({_json.dumps(title)}, {_json.dumps(body)});"
        )
        return
    except Exception:
        pass
    try:
        from aqt.utils import tooltip

        tooltip(f"<b>{title}</b><br>{body}", period=9_000, parent=mw)
    except Exception:
        pass


def start(mw) -> None:
    global _scheduler
    if _scheduler is None:
        _scheduler = _Scheduler(mw)
        _scheduler.start()
    else:
        _scheduler.reschedule()


def reschedule(mw) -> None:
    if _scheduler is None:
        start(mw)
    else:
        _scheduler.reschedule()


def fire_kind(mw, kind: str) -> None:
    """Fire one specific notification type on demand (for the previews gallery),
    using its real copy."""
    try:
        from ante.os_notify import copy_for_kind

        title, body = copy_for_kind(kind)
    except Exception:
        title, body = "Ante", "The table's open."
    if _scheduler is not None:
        _scheduler._fire(title, body)
    else:
        _macos_banner(title, body)
        _in_app_nudge(mw, title, body)


def fire_test(mw) -> None:
    """Fire one notification right now (Settings → 'Send a test'), using the
    next scheduled reminder's real copy so the student sees exactly what a
    nudge will look like."""
    title, body = "Ante", "This is how a nudge from the den will look."
    try:
        col = getattr(mw, "col", None)
        if col is not None:
            from datetime import datetime as _dt

            from ante.reminders import Reminder, next_reminder
            from aqt.ante import build_reminder_schedule

            sched = [
                Reminder(
                    r["hour"],
                    r["minute"],
                    r["window"],
                    r["kind"],
                    r["title"],
                    r["body"],
                )
                for r in build_reminder_schedule(col)
            ]
            now = _dt.now()
            nxt = next_reminder(sched, now.hour, now.minute)
            if nxt is not None:
                title, body = (
                    nxt.title,
                    f"(test · scheduled {nxt.hour:02d}:{nxt.minute:02d}) " + nxt.body,
                )
    except Exception:
        pass
    if _scheduler is not None:
        _scheduler._fire(title, body)
    else:
        _macos_banner(title, body)
        _in_app_nudge(mw, title, body)
