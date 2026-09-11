"""The launcher that keeps a machine on the published build.

A user is handed one shortcut. Whether this machine has the current build, and
fetching the difference if it has not, is the launcher's business - so nobody
copies a folder by hand, and nobody runs last month's build without knowing it.

These run the real script against a share and a machine made of temporary
directories. What they are guarding is not the copying, which robocopy does,
but the two things around it: that an update leaves the operator's own work
alone, and that a share nobody can reach does not stop the application from
starting.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="a Windows launcher")

LAUNCHER = Path(__file__).parents[2] / "packaging" / "launcher" / "launcher.ps1"


def run(script: Path, *arguments) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-CheckOnly", *arguments],
        capture_output=True, text=True, timeout=180)


def publish(share: Path, version: str, exe_text: str = "an application") -> None:
    (share / "app" / "_internal").mkdir(parents=True, exist_ok=True)
    (share / "app" / "CAMDS-IMDS-Importer.exe").write_text(exe_text, encoding="utf-8")
    (share / "app" / "_internal" / "lib.dat").write_text("internals", encoding="utf-8")
    # Written last, as the publishing step writes it: a launcher reading it
    # mid-copy would fetch half a build and record it as the whole one.
    (share / "version.txt").write_text(version, encoding="utf-8")


def machine(tmp_path: Path, share: Path) -> Path:
    here = tmp_path / "machine"
    here.mkdir()
    (here / "launcher.ps1").write_bytes(LAUNCHER.read_bytes())
    (here / "share.txt").write_text(str(share), encoding="utf-8")
    return here


def test_a_machine_is_brought_to_the_published_build_and_kept_there(tmp_path):
    share = tmp_path / "share"
    publish(share, "a8d53fc  built 2026-09-11 17:00")
    here = machine(tmp_path, share)

    first = run(here / "launcher.ps1")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "nothing installed yet" in first.stdout
    assert (here / "app" / "CAMDS-IMDS-Importer.exe").read_text() == "an application"

    again = run(here / "launcher.ps1")
    assert "Up to date" in again.stdout, "nothing is copied twice"


def test_an_update_leaves_the_operators_own_work_alone(tmp_path):
    """The application writes its choices, its sign-in and its journals beside
    itself. A mirror that did not know that would delete all three as files
    that are not on the share."""
    share = tmp_path / "share"
    publish(share, "a8d53fc  built 2026-09-11 17:00")
    here = machine(tmp_path, share)
    run(here / "launcher.ps1")

    for folder, name, content in (("config", "substance_mapping.json", "answers a person typed"),
                                  (".runtime", "camds_storage_state.json", "the saved sign-in"),
                                  ("output", "run.jsonl", "an interrupted import")):
        (here / "app" / folder).mkdir(parents=True, exist_ok=True)
        (here / "app" / folder / name).write_text(content, encoding="utf-8")

    publish(share, "9f8d87a  built 2026-09-12 08:30", exe_text="a newer application")
    update = run(here / "launcher.ps1")

    assert "Updated to 9f8d87a" in update.stdout
    assert (here / "app" / "CAMDS-IMDS-Importer.exe").read_text() == "a newer application"
    assert (here / "app" / "config" / "substance_mapping.json").read_text() == "answers a person typed"
    assert (here / "app" / ".runtime" / "camds_storage_state.json").read_text() == "the saved sign-in"
    assert (here / "app" / "output" / "run.jsonl").read_text() == "an interrupted import"


def test_a_share_nobody_can_reach_does_not_stop_the_application(tmp_path):
    """Working offline, or off the company network, is not a reason to be
    unable to open what is already installed."""
    share = tmp_path / "share"
    publish(share, "a8d53fc  built 2026-09-11 17:00")
    here = machine(tmp_path, share)
    run(here / "launcher.ps1")

    (here / "share.txt").write_text(r"\\nosuchserver\nosuchshare", encoding="utf-8")
    offline = run(here / "launcher.ps1")

    assert offline.returncode == 0
    assert "not reachable" in offline.stdout
    assert "Starting the build this machine already has" in offline.stdout


def test_a_launcher_nobody_has_configured_says_what_to_write_where(tmp_path):
    here = tmp_path / "unconfigured"
    here.mkdir()
    (here / "launcher.ps1").write_bytes(LAUNCHER.read_bytes())

    bare = run(here / "launcher.ps1")
    assert bare.returncode == 2, "the .cmd pauses on this, so the message is read"
    assert "share.txt" in bare.stdout


def test_the_build_publishes_the_version_after_the_files():
    """A launcher that read the version while the copy was still running would
    fetch half a build and record it as the whole one."""
    script = (Path(__file__).parents[2] / "build.ps1").read_text(encoding="utf-8")
    publishing = script[script.index('Step "Publishing'):]
    assert publishing.index("robocopy.exe") < publishing.index("version.txt")
    assert "/XD" not in publishing, "the share holds the build, not a machine's own data"
    assert "launcher.ps1" in publishing, "the launcher is published beside what it fetches"
