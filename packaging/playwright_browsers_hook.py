"""Point Playwright at the browser bundled beside the frozen application.

Runs before any application import, because Playwright reads this variable when
its package is first imported. "0" means "look inside the playwright package"
rather than in the user's `%LOCALAPPDATA%\\ms-playwright`, which is where the
build put it with the same variable set.

Only used by a build made with -IncludeBrowser. Without it, Playwright falls
back to the per-user location, which is what `playwright install` fills.
"""
import os
import sys
from pathlib import Path

_bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "playwright" / "driver" / "package" / ".local-browsers"
if _bundled.is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
