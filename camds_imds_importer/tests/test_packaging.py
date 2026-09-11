r"""What a built executable needs that a checkout does not.

`config/`, `.runtime/` and `output/` are relative paths, which is right when the
app runs from a checkout. A built executable can be started from anywhere - a
Start-menu shortcut runs it from C:\Windows\system32 - and the reviewed
mappings, the saved session and the import journals would be written there.
"""
import sys
from pathlib import Path

from camds_imds_importer.app import working_directory


def test_a_checkout_keeps_its_files_where_it_was_started(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    assert working_directory() == Path.cwd()


def test_a_built_executable_keeps_its_files_beside_itself(monkeypatch, tmp_path):
    """Not in whatever directory the shortcut happened to point at."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "CAMDS.exe"))
    monkeypatch.chdir(tmp_path)
    assert working_directory() == tmp_path / "app"


def test_the_build_script_ships_the_files_read_at_runtime():
    """Two files are read from inside the package; missing either is silent."""
    script = (Path(__file__).parents[2] / "build.ps1").read_text(encoding="utf-8")
    for data in ("material_classifications.json", "camds.ico"):
        assert data in script, f"{data} is read at runtime and must be bundled"
    assert "--collect-all" in script and "playwright" in script




def test_the_browser_location_follows_playwrights_own_rule(monkeypatch, tmp_path):
    """"0" means inside the package, any other value is a path, unset means the
    user's cache. Guessing differently is how a build hunts in the wrong place."""
    from camds_imds_importer.camds import browser_runtime

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "elsewhere"))
    assert browser_runtime.browsers_root() == tmp_path / "elsewhere"

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert browser_runtime.browsers_root().name == ".local-browsers"

    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    assert browser_runtime.browsers_root() == tmp_path / "ms-playwright"


def test_an_empty_directory_is_not_a_browser(monkeypatch, tmp_path):
    """The failing build pointed at a directory that existed and held nothing."""
    from camds_imds_importer.camds import browser_runtime

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    (tmp_path / "chromium-1234" / "chrome-win64").mkdir(parents=True)
    assert not browser_runtime.chromium_present()

    (tmp_path / "chromium-1234" / "chrome-win64" / "chrome.exe").write_bytes(b"")
    assert browser_runtime.chromium_present()


def test_playwright_still_forces_the_bundled_path_on_a_frozen_build():
    """The reason the hook exists. Playwright assumes a frozen application
    carries its browsers and defaults the variable to "0" - inside the package -
    on every launch. If this ever stops being true the hook can go; while it is
    true, clearing the variable is useless because setdefault fills it back in.
    """
    import inspect

    from playwright._impl import _transport

    source = inspect.getsource(_transport)
    assert 'setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")' in source
    assert 'getattr(sys, "frozen", False)' in source


def test_the_runtime_hook_sets_the_variable_rather_than_clearing_it():
    """Clearing it leaves Playwright's setdefault free to choose "0"."""
    hook = (Path(__file__).parents[2] / "packaging" / "playwright_browsers_hook.py")
    source = hook.read_text(encoding="utf-8")
    assert "os.environ.pop" not in source, "clearing is exactly what does not work"
    assert "_user_cache()" in source, "an unbundled build must name the per-user cache"
    assert 'os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"' in source


def test_the_hook_agrees_with_playwrights_own_default_location(monkeypatch, tmp_path):
    """The hook and the app must resolve the same directory, or one of them is
    describing a place the browser is not."""
    import importlib.util

    from camds_imds_importer.camds import browser_runtime

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    spec = importlib.util.spec_from_file_location(
        "hook_probe", Path(__file__).parents[2] / "packaging" / "playwright_browsers_hook.py")
    hook = importlib.util.module_from_spec(spec)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    spec.loader.exec_module(hook)
    assert hook._user_cache() == tmp_path / "ms-playwright"
    assert browser_runtime.browsers_root() == tmp_path / "ms-playwright"


def test_the_build_always_ships_that_hook():
    script = (Path(__file__).parents[2] / "build.ps1").read_text(encoding="utf-8")
    hook_line = [l for l in script.splitlines() if "playwright_browsers_hook" in l]
    assert hook_line, "the hook must be passed to PyInstaller"
    assert not any("IncludeBrowser" in l for l in hook_line), (
        "it is needed for both kinds of build, not only the bundled one")


def test_the_build_can_report_what_it_resolved(capsys, monkeypatch, tmp_path):
    """A built executable offers nothing to inspect when it misbehaves, and the
    first one failed with a path inside its own extraction directory."""
    from camds_imds_importer.app import check_command

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))
    # Pointed at an empty directory, so the launch is expected to fail: what
    # matters is that it says where it looked and what went wrong.
    assert check_command() == 1
    printed = capsys.readouterr().out
    for line in ("Files kept in", "BROWSERS_PATH", "Browser looked up", "Chromium",
                 "Driver env", "Launch"):
        assert line in printed, printed
    assert str(tmp_path / "browsers") in printed
    assert "FAILED" in printed, "a launch that cannot work must not read as fine"


def test_a_build_can_name_the_commit_it_came_from(tmp_path, monkeypatch):
    """A stale executable answered three runs with a defect already fixed in
    the source, and nothing on screen could tell it from a fresh one."""
    from camds_imds_importer import build_info

    monkeypatch.setattr(build_info, "STAMP", tmp_path / "build_stamp.txt")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert build_info.build_stamp() == "running from the source tree"

    (tmp_path / "build_stamp.txt").write_text("abc1234  built 2026-09-07 16:20", encoding="utf-8")
    assert build_info.build_stamp() == "abc1234  built 2026-09-07 16:20"


def test_a_stamp_written_with_a_byte_order_mark_reads_clean(tmp_path, monkeypatch):
    """PowerShell 5.1's Set-Content -Encoding utf8 writes one, and it was shown
    as a stray character in front of the commit."""
    from camds_imds_importer import build_info

    stamp = tmp_path / "build_stamp.txt"
    stamp.write_bytes("﻿782a212  built 2026-09-08 06:52".encode("utf-8"))
    monkeypatch.setattr(build_info, "STAMP", stamp)
    assert build_info.build_stamp() == "782a212  built 2026-09-08 06:52"


def test_an_untracked_file_does_not_make_a_build_look_modified():
    """A screenshot beside the tree is not in the executable."""
    script = (Path(__file__).parents[2] / "build.ps1").read_text(encoding="utf-8")
    assert "git status --porcelain --untracked-files=no" in script
    assert "UTF8Encoding $false" in script, "a stamp without a byte-order mark"
    assert "Set-Content -Path $stampFile" not in script


def test_an_unstamped_executable_says_so_rather_than_claiming_source(tmp_path, monkeypatch):
    from camds_imds_importer import build_info

    monkeypatch.setattr(build_info, "STAMP", tmp_path / "missing.txt")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "rebuild" in build_info.build_stamp()


def test_the_build_ships_the_stamp_and_writes_it_first():
    script = (Path(__file__).parents[2] / "build.ps1").read_text(encoding="utf-8")
    assert "build_stamp.txt" in script
    assert script.index("Stamping the build") < script.index('Step "Building"')


def test_the_stamp_is_not_committed():
    """It describes one machine's build, and would conflict on every rebuild."""
    ignored = (Path(__file__).parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "build_stamp.txt" in ignored


def test_a_blocked_browser_download_says_what_to_do_instead(monkeypatch, tmp_path):
    """The driver answers with a Node stack trace, and that went on screen as
    it stood: "at ChildProcess._handle.onexit (node:internal/child_process)".
    It tells an operator nothing, and both ways out need no download."""
    from camds_imds_importer.camds import browser_runtime

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "ms-playwright"))
    message = browser_runtime._download_failed([
        "Downloading Chromium 140.0 (playwright build v1187)",
        "Error: Download failure, code=1",
        "at ChildProcess.<anonymous> (C:\Temp\_MEI0000\playwright\driver\coreBundle.js:32015:32)",
        "at ChildProcess.emit (node:events:519:28)",
        "at ChildProcess._handle.onexit (node:internal/child_process:295:12)",
    ])

    assert "-IncludeBrowser" in message, "the build that carries the browser"
    assert "HTTPS_PROXY" in message, "the usual reason a download is blocked"
    assert str(tmp_path / "ms-playwright") in message, "where to copy a browser to"
    assert "Error: Download failure, code=1" in message, "what the downloader said"
    assert "onexit" not in message, "the stack frames are noise"


def test_stack_frames_are_not_reported_as_progress():
    """Each one replaced the status line while the download was failing."""
    import inspect

    from camds_imds_importer.camds import browser_runtime

    source = inspect.getsource(browser_runtime.install_chromium)
    assert 'line.startswith(("at ", "Error:", "code="))' in source
