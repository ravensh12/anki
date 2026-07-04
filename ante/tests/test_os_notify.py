# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for OS-scheduled reminders (app-closed delivery)."""

import plistlib

from ante.os_notify import (
    ReminderJob,
    install,
    installed_jobs,
    jobs_from_schedule,
    launchd_plist,
    osascript_notification,
    powershell_toast_script,
    systemd_units,
    uninstall_all,
)


class _Run:
    """Records commands instead of executing them."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, check=False, **kw):
        self.calls.append(list(cmd))

        class R:
            returncode = 0

        return R()


_SCHEDULE = [
    {"kind": "retrieval", "hour": 7, "minute": 30, "at": "07:30"},
    {"kind": "review", "hour": 14, "minute": 0, "at": "14:00"},
    {"kind": "encode", "hour": 21, "minute": 30, "at": "21:30"},
]


def test_jobs_from_schedule_maps_windows_to_evergreen_copy():
    jobs = jobs_from_schedule(_SCHEDULE)
    assert [j.key for j in jobs] == ["morning", "midday", "night"]
    morning = jobs[0]
    assert morning.at == "07:30"
    assert "morning game" in morning.title
    night = jobs[2]
    assert "lights out" in night.title
    assert "overnight" in night.body


def test_launchd_plist_fires_notification_at_the_right_time():
    job = ReminderJob("morning", 7, 30, "First Light", "Start cold.")
    data = plistlib.loads(launchd_plist(job))
    assert data["Label"] == "app.ante.reminder.morning"
    assert data["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert data["ProgramArguments"][0] == "/usr/bin/osascript"
    assert "display notification" in data["ProgramArguments"][2]


def test_osascript_escapes_quotes():
    s = osascript_notification('He said "go"', 'It\'s "time"')
    assert '\\"go\\"' in s and '\\"time\\"' in s


def test_macos_install_writes_agents_and_bootstraps(tmp_path):
    run = _Run()
    out = install(_SCHEDULE, platform="darwin", home=tmp_path, run=run)
    assert out["ok"] and len(out["installed"]) == 3
    agents = tmp_path / "Library" / "LaunchAgents"
    plists = sorted(p.name for p in agents.glob("*.plist"))
    assert plists == [
        "app.ante.reminder.midday.plist",
        "app.ante.reminder.morning.plist",
        "app.ante.reminder.night.plist",
    ]
    assert any("bootstrap" in c for call in run.calls for c in call)
    assert installed_jobs(platform="darwin", home=tmp_path) == [
        "app.ante.reminder.midday",
        "app.ante.reminder.morning",
        "app.ante.reminder.night",
    ]


def test_macos_uninstall_removes_everything(tmp_path):
    run = _Run()
    install(_SCHEDULE, platform="darwin", home=tmp_path, run=run)
    out = uninstall_all(platform="darwin", home=tmp_path, run=run)
    assert out["ok"] and out["removed"] == 3
    assert installed_jobs(platform="darwin", home=tmp_path) == []


def test_empty_schedule_uninstalls_instead_of_installing(tmp_path):
    run = _Run()
    install(_SCHEDULE, platform="darwin", home=tmp_path, run=run)
    out = install([], platform="darwin", home=tmp_path, run=run)
    assert out["ok"] and out["installed"] == []
    assert installed_jobs(platform="darwin", home=tmp_path) == []


def test_windows_toast_script_and_task(tmp_path):
    run = _Run()
    out = install(_SCHEDULE, platform="win32", base_dir=tmp_path, run=run)
    assert out["ok"] and len(out["installed"]) == 3
    scripts = sorted(p.name for p in (tmp_path / "notify").glob("*.ps1"))
    assert scripts == ["midday.ps1", "morning.ps1", "night.ps1"]
    body = (tmp_path / "notify" / "morning.ps1").read_text()
    assert "ToastNotificationManager" in body and "Ante" in body
    creates = [c for c in run.calls if c[:2] == ["schtasks", "/Create"]]
    assert len(creates) == 3
    assert any("07:30" in " ".join(c) for c in creates)


def test_powershell_quoting_is_safe():
    job = ReminderJob("night", 21, 0, "Don't stop", "It's fine")
    script = powershell_toast_script(job)
    assert "'Don''t stop'" in script and "'It''s fine'" in script


def test_linux_units_and_timers(tmp_path):
    run = _Run()
    out = install(_SCHEDULE, platform="linux", home=tmp_path, run=run)
    assert out["ok"] and len(out["installed"]) == 3
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    timers = sorted(p.name for p in unit_dir.glob("*.timer"))
    assert timers == [
        "ante-reminder-midday.timer",
        "ante-reminder-morning.timer",
        "ante-reminder-night.timer",
    ]
    service, timer = systemd_units(ReminderJob("morning", 7, 30, "T", "B"))
    assert "notify-send" in service
    assert "OnCalendar=*-*-* 07:30:00" in timer
    assert uninstall_all(platform="linux", home=tmp_path, run=run)["removed"] == 3
