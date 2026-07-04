# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""OS-scheduled study reminders — the bookends fire even when Ante is closed.

The in-app scheduler (qt/aqt/ante_reminders.py) can only nudge while the
app is running, which defeats the whole point of a *when-to-study* system: the
morning cue must arrive before you've opened anything. This module registers
the day's reminder times with the operating system's own scheduler, so First
Light and Last Light arrive like an alarm clock:

  * macOS   — per-reminder launchd user agents (``~/Library/LaunchAgents``)
              whose ``StartCalendarInterval`` runs ``osascript`` to post a real
              Notification Center banner (with a soft sound).
  * Windows — daily Task Scheduler jobs (``schtasks``) running a generated
              PowerShell toast script.
  * Linux   — systemd user timers running ``notify-send``.

Design rules: opt-in (autonomy), quiet-hours respected upstream (suppressed
windows never produce a job), no-shame copy baked in, and fully reversible —
``uninstall_all`` removes every artifact. Pure content-generation is separated
from execution so it is unit-testable without touching the OS: ``install`` and
``uninstall_all`` take an injectable runner.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LABEL_PREFIX = "app.ante.reminder"
TASK_FOLDER = "Ante"  # Windows Task Scheduler folder
UNIT_PREFIX = "ante-reminder"  # systemd unit prefix


@dataclass(frozen=True)
class ReminderJob:
    key: str  # stable slug, e.g. "morning"
    hour: int
    minute: int
    title: str
    body: str

    @property
    def label(self) -> str:
        return f"{LABEL_PREFIX}.{self.key}"

    @property
    def at(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


# evergreen copy per reminder kind (no live due-counts — safe for OS-scheduled
# jobs and for previews). Shared so the copy is defined once.
EVERGREEN: dict[str, tuple[str, str]] = {
    "retrieval": (
        "The morning game opens",
        "Sit down cold: a short recall hand beats an hour of rereading. "
        "The deck is already stacked in your favor.",
    ),
    "review": (
        "Midday — protect your stack",
        "A few minutes now and the House doesn't claw back what you banked.",
    ),
    "encode": (
        "The midnight game — last hand before lights out",
        "Play a light hand now and your brain banks it overnight. Then lights out.",
    ),
}
# a fourth, non-scheduled surface: the surprise/mastery-reward nudge
REWARD_COPY = (
    "A table just went your way",
    "You proved a topic on application — that plaque is yours, banked. "
    "(Surprise reward, if rewards are on.)",
)


def preview_notifications() -> list[dict]:
    """Every notification the app can send, for a preview gallery."""
    default_at = {"retrieval": "08:00", "review": "14:00", "encode": "21:00"}
    out = []
    for kind, (title, body) in EVERGREEN.items():
        out.append(
            {"kind": kind, "at": default_at.get(kind, ""), "title": title, "body": body}
        )
    out.append({"kind": "reward", "at": "on mastery", "title": REWARD_COPY[0], "body": REWARD_COPY[1]})
    return out


def copy_for_kind(kind: str) -> tuple[str, str]:
    if kind == "reward":
        return REWARD_COPY
    return EVERGREEN.get(kind, ("Ante", "The table's open."))


def jobs_from_schedule(schedule: list[dict]) -> list[ReminderJob]:
    """Turn the reminder schedule (reminders.build_schedule dicts) into OS jobs.

    The copy is evergreen (no live due-counts — the OS can't know them), while
    keeping the learning-science cue of each window."""
    evergreen = EVERGREEN
    out: list[ReminderJob] = []
    seen: set[str] = set()
    for r in schedule or []:
        kind = str(r.get("kind", "review"))
        key = {"retrieval": "morning", "encode": "night"}.get(kind, "midday")
        if key in seen:
            continue
        seen.add(key)
        title, body = evergreen.get(kind, evergreen["review"])
        out.append(
            ReminderJob(
                key=key,
                hour=int(r.get("hour", 0)),
                minute=int(r.get("minute", 0)),
                title=title,
                body=body,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# macOS (launchd + osascript)
# --------------------------------------------------------------------------- #


def _applescript_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def osascript_notification(title: str, body: str, sound: str = "Glass") -> str:
    return (
        f'display notification "{_applescript_quote(body)}" '
        f'with title "{_applescript_quote(title)}" sound name "{sound}"'
    )


def launchd_plist(job: ReminderJob) -> bytes:
    return plistlib.dumps(
        {
            "Label": job.label,
            "ProgramArguments": [
                "/usr/bin/osascript",
                "-e",
                osascript_notification(job.title, job.body),
            ],
            "StartCalendarInterval": {"Hour": job.hour, "Minute": job.minute},
            "RunAtLoad": False,
        }
    )


def _launch_agents_dir(home: Path) -> Path:
    return home / "Library" / "LaunchAgents"


def _install_macos(jobs: list[ReminderJob], home: Path, run) -> list[str]:
    agents = _launch_agents_dir(home)
    agents.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    installed = []
    for job in jobs:
        path = agents / f"{job.label}.plist"
        # bootout first so a changed time re-registers cleanly
        run(["launchctl", "bootout", f"gui/{uid}/{job.label}"], check=False)
        path.write_bytes(launchd_plist(job))
        res = run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=False)
        if getattr(res, "returncode", 0) != 0:  # older macOS fallback
            run(["launchctl", "load", "-w", str(path)], check=False)
        installed.append(f"{job.at} {job.title}")
    return installed


def _uninstall_macos(home: Path, run) -> int:
    agents = _launch_agents_dir(home)
    uid = os.getuid()
    n = 0
    if agents.is_dir():
        for path in agents.glob(f"{LABEL_PREFIX}.*.plist"):
            run(["launchctl", "bootout", f"gui/{uid}/{path.stem}"], check=False)
            run(["launchctl", "unload", str(path)], check=False)
            path.unlink(missing_ok=True)
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Windows (Task Scheduler + PowerShell toast)
# --------------------------------------------------------------------------- #

_PS_TOAST = r"""
$title = {title!r}
$body = {body!r}
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast><visual><binding template="ToastText02"><text id="1">$title</text><text id="2">$body</text></binding></visual></toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Ante").Show($toast)
""".strip()


def powershell_toast_script(job: ReminderJob) -> str:
    # repr() gives PowerShell-safe single-quoted-ish literals for our copy
    # (no embedded quotes in the evergreen strings; keep it simple and safe)
    def ps_quote(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    return _PS_TOAST.replace("{title!r}", ps_quote(job.title)).replace(
        "{body!r}", ps_quote(job.body)
    )


def _install_windows(jobs: list[ReminderJob], base_dir: Path, run) -> list[str]:
    scripts = base_dir / "notify"
    scripts.mkdir(parents=True, exist_ok=True)
    installed = []
    for job in jobs:
        ps1 = scripts / f"{job.key}.ps1"
        ps1.write_text(powershell_toast_script(job), encoding="utf-8")
        task = f"{TASK_FOLDER}\\{job.key}"
        run(
            [
                "schtasks",
                "/Create",
                "/F",
                "/SC",
                "DAILY",
                "/TN",
                task,
                "/ST",
                job.at,
                "/TR",
                f'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1}"',
            ],
            check=False,
        )
        installed.append(f"{job.at} {job.title}")
    return installed


def _uninstall_windows(base_dir: Path, run) -> int:
    n = 0
    for key in ("morning", "midday", "night"):
        res = run(
            ["schtasks", "/Delete", "/F", "/TN", f"{TASK_FOLDER}\\{key}"],
            check=False,
        )
        if getattr(res, "returncode", 1) == 0:
            n += 1
    scripts = base_dir / "notify"
    if scripts.is_dir():
        for p in scripts.glob("*.ps1"):
            p.unlink(missing_ok=True)
    return n


# --------------------------------------------------------------------------- #
# Linux (systemd user timers + notify-send)
# --------------------------------------------------------------------------- #


def systemd_units(job: ReminderJob) -> tuple[str, str]:
    service = (
        "[Unit]\nDescription=Ante study reminder\n\n[Service]\nType=oneshot\n"
        f"ExecStart=/usr/bin/env notify-send -a Ante {_sh_quote(job.title)} {_sh_quote(job.body)}\n"
    )
    timer = (
        "[Unit]\nDescription=Ante study reminder timer\n\n[Timer]\n"
        f"OnCalendar=*-*-* {job.at}:00\nPersistent=false\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    return service, timer


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _systemd_dir(home: Path) -> Path:
    return home / ".config" / "systemd" / "user"


def _install_linux(jobs: list[ReminderJob], home: Path, run) -> list[str]:
    unit_dir = _systemd_dir(home)
    unit_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    for job in jobs:
        name = f"{UNIT_PREFIX}-{job.key}"
        service, timer = systemd_units(job)
        (unit_dir / f"{name}.service").write_text(service, encoding="utf-8")
        (unit_dir / f"{name}.timer").write_text(timer, encoding="utf-8")
        installed.append(f"{job.at} {job.title}")
    run(["systemctl", "--user", "daemon-reload"], check=False)
    for job in jobs:
        run(
            [
                "systemctl",
                "--user",
                "enable",
                "--now",
                f"{UNIT_PREFIX}-{job.key}.timer",
            ],
            check=False,
        )
    return installed


def _uninstall_linux(home: Path, run) -> int:
    unit_dir = _systemd_dir(home)
    n = 0
    if unit_dir.is_dir():
        for p in list(unit_dir.glob(f"{UNIT_PREFIX}-*.timer")) + list(
            unit_dir.glob(f"{UNIT_PREFIX}-*.service")
        ):
            if p.suffix == ".timer":
                run(["systemctl", "--user", "disable", "--now", p.name], check=False)
                n += 1
            p.unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"], check=False)
    return n


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def install(
    schedule: list[dict],
    *,
    platform: str | None = None,
    home: Path | None = None,
    base_dir: Path | None = None,
    run=subprocess.run,
) -> dict:
    """(Re)register the day's reminders with the OS scheduler.

    Idempotent: existing Ante jobs are replaced. Returns what was installed
    so the UI can show it honestly."""
    platform = platform or sys.platform
    home = home or Path.home()
    base_dir = base_dir or home / ".ante"
    jobs = jobs_from_schedule(schedule)
    if not jobs:
        uninstall_all(platform=platform, home=home, base_dir=base_dir, run=run)
        return {"ok": True, "installed": [], "platform": platform}
    try:
        if platform == "darwin":
            installed = _install_macos(jobs, home, run)
        elif platform.startswith("win"):
            installed = _install_windows(jobs, base_dir, run)
        else:
            installed = _install_linux(jobs, home, run)
        return {"ok": True, "installed": installed, "platform": platform}
    except Exception as exc:  # never take the app down over a nudge
        return {"ok": False, "error": str(exc), "platform": platform}


def uninstall_all(
    *,
    platform: str | None = None,
    home: Path | None = None,
    base_dir: Path | None = None,
    run=subprocess.run,
) -> dict:
    """Remove every Ante OS-scheduler artifact (fully reversible)."""
    platform = platform or sys.platform
    home = home or Path.home()
    base_dir = base_dir or home / ".ante"
    try:
        if platform == "darwin":
            n = _uninstall_macos(home, run)
        elif platform.startswith("win"):
            n = _uninstall_windows(base_dir, run)
        else:
            n = _uninstall_linux(home, run)
        return {"ok": True, "removed": n, "platform": platform}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "platform": platform}


def installed_jobs(
    *, platform: str | None = None, home: Path | None = None
) -> list[str]:
    """The Ante jobs currently registered (by artifact inspection)."""
    platform = platform or sys.platform
    home = home or Path.home()
    if platform == "darwin":
        agents = _launch_agents_dir(home)
        return sorted(p.stem for p in agents.glob(f"{LABEL_PREFIX}.*.plist"))
    if platform.startswith("win"):
        return []  # schtasks query is slow; the toggle state is the source of truth
    unit_dir = _systemd_dir(home)
    return sorted(p.stem for p in unit_dir.glob(f"{UNIT_PREFIX}-*.timer"))
