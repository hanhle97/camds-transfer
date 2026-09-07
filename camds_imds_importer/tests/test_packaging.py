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


def test_the_runtime_hook_clears_the_variable_when_nothing_is_bundled():
    """A machine with a stray PLAYWRIGHT_BROWSERS_PATH=0 sent the first build
    looking for chrome.exe inside its own extraction directory."""
    hook = (Path(__file__).parents[2] / "packaging" / "playwright_browsers_hook.py")
    source = hook.read_text(encoding="utf-8")
    assert 'os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)' in source
    assert "chrome.exe" in source, "presence must be judged on the executable, not the folder"


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
    assert check_command() == 0
    printed = capsys.readouterr().out
    for line in ("Files kept in", "BROWSERS_PATH", "Browser looked up", "Chromium"):
        assert line in printed, printed
    assert str(tmp_path / "browsers") in printed
