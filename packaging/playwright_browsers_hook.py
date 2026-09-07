r"""Tell a frozen build where Chromium is, before Playwright decides for it.

Playwright assumes a frozen application bundles its browsers. `_transport.py`
does, on every launch:

    if getattr(sys, "frozen", False) or globals().get("__compiled__"):
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

and "0" means "inside the playwright package". A build that does not carry a
browser therefore goes looking inside its own extraction directory, finds
nothing, and reports a path in %TEMP%\_MEIxxxxxx that means nothing to anybody.
Clearing the variable does not help: `setdefault` fills it straight back in.

The only thing that does is setting it, so there is nothing to default. Set to
the bundled browser when one is really there, and otherwise to the same
per-user cache `playwright install` writes to - the directory Playwright's own
driver would have chosen had it not been frozen.

An operator who sets the variable themselves is left alone.
"""
import os
import sys
from pathlib import Path

_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
_bundled = _root / "playwright" / "driver" / "package" / ".local-browsers"


def _user_cache() -> Path:
    """Playwright's own default, kept in step with its driver."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(local) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"


if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    pass  # deliberately chosen elsewhere; leave it
elif any(_bundled.glob("chromium*/*/chrome*")):
    # A directory is not a browser: only an executable under it counts.
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
else:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_user_cache())
