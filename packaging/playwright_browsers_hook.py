r"""Decide where a frozen build looks for Chromium, before Playwright asks.

Playwright's driver reads PLAYWRIGHT_BROWSERS_PATH: "0" means "inside the
playwright package", anything else is taken as a path, and unset means the
user's own cache (%LOCALAPPDATA%\ms-playwright). This runs before any import so
the answer is settled once.

Both halves matter. A build made with -IncludeBrowser must point at what it
carries. A build without one must point at the user's cache even if the machine
happens to have PLAYWRIGHT_BROWSERS_PATH=0 left over from something else - which
is exactly what sent the first build looking for chrome.exe inside its own
extraction directory, where nothing had been bundled.
"""
import os
import sys
from pathlib import Path

_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
_bundled = _root / "playwright" / "driver" / "package" / ".local-browsers"

# An empty or half-collected directory is not a browser: only a real chrome
# executable underneath it counts.
if any(_bundled.glob("chromium-*/chrome-win*/chrome.exe")):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
else:
    os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
