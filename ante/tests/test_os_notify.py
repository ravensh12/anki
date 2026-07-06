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


# --------------------------------------------------------------------------- #
# Marked nights (quiz checkpoints / full-lengths): date-scoped one-shot jobs
# --------------------------------------------------------------------------- #

_MARKED = {
    "kind": "checkpoint",
    "hour": 17,
    "minute": 0,
    "at": "17:00",
    "date": "2026-07-19",
    "title": "Marked night — the quiz checkpoint",
    "body": "Re-take the section quizzes tonight and re-measure your baseline.",
}


def test_checkpoint_entry_becomes_a_dated_job_with_plan_copy():
    jobs = jobs_from_schedule(_SCHEDULE + [_MARKED])
    assert [j.key for j in jobs] == ["morning", "midday", "night", "checkpoint"]
    cp = jobs[-1]
    assert cp.date == "2026-07-19" and cp.ymd == (2026, 7, 19)
    # the plan-computed copy (calendar facts) is carried, not evergreen copy
    assert "quiz checkpoint" in cp.title
    # daily jobs stay undated
    assert all(j.date is None for j in jobs[:-1])


def test_checkpoint_without_a_date_is_never_registered():
    # a daily-recurring "checkpoint tonight" banner would be a lie
    assert jobs_from_schedule([{"kind": "checkpoint", "hour": 17, "minute": 0}]) == []


def test_macos_dated_job_pins_month_and_day():
    jobs = jobs_from_schedule([_MARKED])
    data = plistlib.loads(launchd_plist(jobs[0]))
    assert data["StartCalendarInterval"] == {
        "Hour": 17,
        "Minute": 0,
        "Month": 7,
        "Day": 19,
    }


def test_linux_dated_timer_fires_on_the_marked_night_only():
    (job,) = jobs_from_schedule([_MARKED])
    _service, timer = systemd_units(job)
    assert "OnCalendar=2026-07-19 17:00:00" in timer


def test_windows_dated_task_is_scheduled_once(tmp_path):
    run = _Run()
    out = install(_SCHEDULE + [_MARKED], platform="win32", base_dir=tmp_path, run=run)
    assert out["ok"] and len(out["installed"]) == 4
    creates = [c for c in run.calls if c[:2] == ["schtasks", "/Create"]]
    once = [c for c in creates if "ONCE" in c]
    assert len(once) == 1
    assert "07/19/2026" in once[0] and "17:00" in once[0]
    # the daily jobs stay daily
    assert sum(1 for c in creates if "DAILY" in c) == 3
    # uninstall clears the checkpoint task alongside the daily ones
    out = uninstall_all(platform="win32", base_dir=tmp_path, run=run)
    assert out["ok"] and out["removed"] == 4
