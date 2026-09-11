"""Reaching the person who maintains this program.

An operator who hits something they cannot get past has the Logs tab, and no
idea what to do with it. This turns that into a mail addressed to the developer
with the things that are always asked for anyway: which build it is, what
report was open, and the last of the log.

Outlook is asked directly where the machine has one. Handing a mailto: to the
shell opens whatever claims the scheme, and on a managed machine that is often
a browser: the operator asked for a mail and got a web page.

Nothing is sent from here. The message opens on screen, the operator adds what
only they know, and they decide whether to send it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

DEVELOPER = "hanh.levan@vn.bosch.com"

# A mailto is passed to the shell as one string, and a long one is cut short -
# silently, and in the middle of a word. The tail of the log is the part worth
# having, so what is dropped is the beginning of it.
LIMIT = 1800


# Where Windows records the path to Outlook, whichever Office this is.
APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE"


def outlook() -> Path | None:
    """Outlook's own executable, if this machine has one.

    Handing a mailto: to the shell opens whatever claims the scheme, which on a
    managed machine is often a browser - the operator asked for a mail and got a
    web page. Outlook, asked directly, opens a message.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows only
        return None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            found = Path(winreg.QueryValue(root, APP_PATHS))
        except OSError:
            continue
        if found.is_file():
            return found
    # Registered nowhere, but installed in one of the places Office uses.
    for base in (r"C:\Program Files\Microsoft Office", r"C:\Program Files (x86)\Microsoft Office"):
        for office in sorted(Path(base).glob("*/OUTLOOK.EXE"), reverse=True):
            return office
    return None


def outlook_command(exe, *, address: str, subject: str, body: str, attachment=None) -> list[str]:
    """Outlook's own switches for "open a new message like this".

    `/c ipm.note` is a new mail, `/m` fills it in - and it takes the mailto
    without the scheme - and `/a` attaches a file, which is how the whole log
    travels instead of as much of it as a command line will carry.
    """
    filled = f"{address}?subject={quote(subject)}&body={quote(body)}"
    command = [str(exe), "/c", "ipm.note", "/m", filled]
    if attachment:
        command += ["/a", str(attachment)]
    return command


def open_in_outlook(exe, **message) -> None:
    """Start Outlook on a new message. It is not waited for."""
    subprocess.Popen(outlook_command(exe, **message),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def subject_of(build: str) -> str:
    return f"CAMDS IMDS Importer - problem report ({build.split()[0] if build else 'unknown'})"


def body(*, build: str, report_name: str, stage: str, log: str, attached: bool = False) -> str:
    """What the mail says, with room at the top for what only a person knows."""
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
        "(attached in full)" if attached else _tail(log),
    ]
    return chr(10).join(lines)


def report(*, build: str, report_name: str, stage: str, log: str) -> str:
    """A mailto: URL, for machines with no Outlook to ask directly."""
    text = body(build=build, report_name=report_name, stage=stage, log=log)
    return f"mailto:{DEVELOPER}?subject={quote(subject_of(build))}&body={quote(text)}"


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
