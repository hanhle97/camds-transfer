"""Reaching the person who maintains this program.

An operator who hits something they cannot get past has the Logs tab, and no
idea what to do with it. This turns that into a mail addressed to the developer
with the things that are always asked for anyway: which build it is, what
report was open, and the last of the log.

The mail is opened in whatever mail program the machine uses and is never sent
from here: the operator sees it, adds what they know, and decides whether to
send it.
"""
from __future__ import annotations

from urllib.parse import quote

DEVELOPER = "hanh.levan@vn.bosch.com"

# A mailto is passed to the shell as one string, and a long one is cut short -
# silently, and in the middle of a word. The tail of the log is the part worth
# having, so what is dropped is the beginning of it.
LIMIT = 1800


def report(*, build: str, report_name: str, stage: str, log: str) -> str:
    """A mailto: URL for a problem report, filled in as far as it can be."""
    subject = f"CAMDS IMDS Importer - problem report ({build.split()[0] if build else 'unknown'})"
    lines = [
        "What I was doing:",
        "",
        "",
        "What happened instead:",
        "",
        "",
        "-- filled in by the program --",
        f"Build      : {build or 'unknown'}",
        f"Report     : {report_name or 'none open'}",
        f"Last stage : {stage or 'idle'}",
        "",
        "Log:",
        _tail(log),
    ]
    body = chr(10).join(lines)
    return f"mailto:{DEVELOPER}?subject={quote(subject)}&body={quote(body)}"


def _tail(log: str) -> str:
    """The end of the log, whole lines only, short enough to survive the shell."""
    text = (log or "").strip()
    if not text:
        return "(empty)"
    kept: list[str] = []
    for line in reversed(text.splitlines()):
        if sum(len(one) + 1 for one in kept) + len(line) > LIMIT:
            kept.append("... earlier lines left out; the Logs tab has all of them")
            break
        kept.append(line)
    return chr(10).join(reversed(kept))
