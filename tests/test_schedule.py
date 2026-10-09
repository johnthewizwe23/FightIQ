import plistlib
import subprocess

from fightiq import schedule


def test_install_writes_a_weekly_launchd_job(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(schedule.sys, "platform", "darwin")
    monkeypatch.setattr(schedule, "PLIST", tmp_path / "com.fightiq.weekly.plist")
    monkeypatch.setattr(schedule, "LOG", tmp_path / "logs" / "weekly.log")
    monkeypatch.setattr(schedule, "_launchctl", lambda *a: calls.append(a) or subprocess.CompletedProcess(a, 0))
    msg = schedule.install("thu", "18:30")
    job = plistlib.loads(schedule.PLIST.read_bytes())
    assert job["StartCalendarInterval"] == {"Weekday": 4, "Hour": 18, "Minute": 30}
    assert job["ProgramArguments"][1:] == ["-m", "fightiq", "weekly"]
    assert calls[-1][0] == "bootstrap" and "Thu at 18:30" in msg
    assert "runs every Thu at 18:30" in schedule.status()
    assert schedule.remove() == "weekly schedule removed" and not schedule.PLIST.exists()
