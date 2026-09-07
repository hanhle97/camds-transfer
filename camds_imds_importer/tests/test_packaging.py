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


def test_the_runtime_hook_only_claims_a_browser_that_is_there():
    """Setting PLAYWRIGHT_BROWSERS_PATH with nothing bundled would make
    Playwright look in an empty directory instead of the one it filled."""
    hook = (Path(__file__).parents[2] / "packaging" / "playwright_browsers_hook.py")
    source = hook.read_text(encoding="utf-8")
    assert "is_dir()" in source, "the variable must be set only when the browser is present"
