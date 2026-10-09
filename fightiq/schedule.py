"""Run the weekly FightIQ routine automatically on a Mac (a per-user launchd job).

    python3 -m fightiq schedule                    # every Thursday at 18:00 (your Mac's time)
    python3 -m fightiq schedule --day sat --time 09:30
    python3 -m fightiq schedule --off              # stop
    python3 -m fightiq schedule --status

The Mac has to be switched on and logged in; if it was asleep at that time, launchd runs the job when it
wakes. Output goes to output/logs/weekly.log.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import config

LABEL = "com.fightiq.weekly"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = config.OUTPUT_DIR / "logs" / "weekly.log"
DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _launchctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _require_mac() -> None:
    if sys.platform != "darwin":
        raise SystemExit("scheduling uses macOS launchd - run this on your Mac")


def install(day: str = "thu", time: str = "18:00") -> str:
    _require_mac()
    if day[:3].lower() not in DAYS:
        raise SystemExit(f"--day must be one of {', '.join(DAYS)}")
    hour, minute = (int(x) for x in time.split(":"))
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        # the same Python that ran this command, so the installed packages are found
        "ProgramArguments": [sys.executable, "-m", "fightiq", "weekly"],
        "WorkingDirectory": str(config.ROOT),
        "StartCalendarInterval": {"Weekday": DAYS[day[:3].lower()], "Hour": hour, "Minute": minute},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                 "PYTHONUNBUFFERED": "1"},
    }
    remove(quiet=True)
    with open(PLIST, "wb") as fh:
        plistlib.dump(plist, fh)
    domain = f"gui/{os.getuid()}"
    if _launchctl("bootstrap", domain, str(PLIST)).returncode != 0:
        _launchctl("load", "-w", str(PLIST))
    return f"FightIQ will run every {day[:3].title()} at {hour:02d}:{minute:02d}. Log: {LOG}"


def remove(quiet: bool = False) -> str:
    _require_mac()
    if PLIST.exists():
        if _launchctl("bootout", f"gui/{os.getuid()}", str(PLIST)).returncode != 0:
            _launchctl("unload", "-w", str(PLIST))
        PLIST.unlink()
        return "weekly schedule removed"
    return "" if quiet else "no weekly schedule was set"


def status() -> str:
    if not PLIST.exists():
        return "no weekly schedule set - turn it on with: python3 -m fightiq schedule"
    with open(PLIST, "rb") as fh:
        cal = plistlib.load(fh)["StartCalendarInterval"]
    day = next(k for k, v in DAYS.items() if v == cal["Weekday"]).title()
    lines = [f"runs every {day} at {cal['Hour']:02d}:{cal['Minute']:02d}"]
    if LOG.exists():
        tail = LOG.read_text(errors="ignore").strip().splitlines()[-8:]
        lines += ["last log lines:"] + [f"  {l}" for l in tail]
    return "\n".join(lines)


def notify(title: str, message: str) -> None:
    """macOS notification banner (does nothing elsewhere)."""
    if sys.platform != "darwin":
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                   capture_output=True)
