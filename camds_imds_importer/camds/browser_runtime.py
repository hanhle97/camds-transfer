r"""Make sure the Chromium Playwright drives is actually on this machine.

A built executable carries Playwright's driver but usually not its browser: the
browser is 430 MB, and bundling it costs more than downloading it once. Without
it, launching a window fails with

    Executable doesn't exist at ...\.local-browsers\chromium-1234\chrome.exe
    Please run the following command to download new browsers: playwright install

which is unhelpful advice for someone who has no Python and no `playwright`
command. The driver can install it itself, and that is what this does.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def browsers_root() -> Path:
    r"""Where Playwright's driver will look, by its own rule.

    "0" means inside the playwright package; any other value is a path; unset
    means the user's cache. Kept in step with the driver rather than guessed.
    """
    from playwright._impl._driver import compute_driver_executable

    where = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if where == "0":
        _, cli = compute_driver_executable()
        return Path(cli).parent / ".local-browsers"
    if where:
        return Path(where)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ms-playwright"


def chromium_present() -> bool:
    """A directory is not a browser; an executable inside one is."""
    root = browsers_root()
    patterns = ("chromium-*/chrome-win*/chrome.exe",
                "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-linux/chrome")
    return any(next(root.glob(pattern), None) for pattern in patterns)


def _download_failed(lines) -> str:
    """What to tell someone whose machine cannot fetch the browser.

    The driver answers a blocked download with a Node stack trace, which was put
    on screen as it stood. It says nothing about what to do, and the two things
    that can be done need no download at all.
    """
    said = " ".join(line for line in lines if line.startswith(("Error:", "Failed", "code=")))
    where = browsers_root()
    return chr(10).join([
        "Could not download the browser this program drives CAMDS through.",
        "",
        f"It is fetched from Playwright's download server into {where}. A network that",
        "reaches the internet through a proxy blocks that unless HTTPS_PROXY is set",
        "before the program starts.",
        "",
        "Two ways round it, neither of which downloads anything:",
        "  - use a build made with -IncludeBrowser, which carries the browser inside it;",
        f"  - copy {where} from a machine that already has it, into the same place here.",
        "",
        "The downloader said: " + ((said or (lines[-1] if lines else ""))[:300] or "nothing"),
    ])


def install_chromium(on_output=None, timeout: float = 1800) -> None:
    """Download Chromium with Playwright's own driver.

    The driver ships with the build, so this needs neither Python nor the
    `playwright` command on the machine - only the network.
    """
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    node, cli = compute_driver_executable()
    if not Path(node).is_file():
        raise RuntimeError(
            f"Playwright's driver is missing from this build ({node}). "
            "Rebuild with build.ps1, which collects it.")
    process = subprocess.Popen(
        [node, cli, "install", "chromium"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=get_driver_env(),
        # Otherwise a windowed build flashes a console for every line.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
    lines: list[str] = []
    for line in process.stdout or ():
        line = line.strip()
        if not line:
            continue
        lines.append(line)
        # A stack frame is not progress. The driver prints a dozen of them when
        # a download fails, and they went to the status line one after another,
        # leaving "Current stage: at ChildProcess._handle.onexit" on screen.
        if on_output and not line.startswith(("at ", "Error:", "code=")):
            on_output(line)
    if process.wait(timeout=timeout) != 0:
        raise RuntimeError(_download_failed(lines))
    if not chromium_present():
        raise RuntimeError(
            "The browser reported itself installed but is not in " + str(browsers_root())
            + ". Copy that folder from a machine that has it, or use a build made with "
              "-IncludeBrowser.")
