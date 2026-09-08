"""What to tell the operator when an import ends.

An import of the real report runs for hours, so nobody is watching when it
finishes. Until now the outcome went to a status line and the log: a run that
had published 52 Materials and one that had died on the first node looked the
same from across the room.

The summary is the same shape either way, because the useful question after a
failure is the same as after a success - how far did it get, and what did it
touch.
"""
from __future__ import annotations


def duration(seconds: float) -> str:
    """A length of time as somebody would say it."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _counted(notes, word) -> int:
    return sum(1 for note in notes if word in note)


def summary(result: dict, *, parse_seconds: float, import_seconds: float,
            nodes: int = 0) -> tuple[str, str]:
    """A title and a body for a finished import."""
    findings = list(result.get("skipped") or [])
    warnings = list(result.get("warnings") or [])
    lines = [
        f"{result.get('nodes', 0)} of {result.get('total', 0)} steps verified.",
        "",
        f"Parsed {nodes} nodes in {duration(parse_seconds)}.",
        f"Imported in {duration(import_seconds)}.",
    ]
    reused = _counted(findings, "reused ")
    released = _counted(findings, "released as ")
    left_out = [f for f in findings if "not imported, at " in f]
    if reused:
        lines.append(f"{reused} Material(s) already in CAMDS were reused.")
    if released:
        lines.append(f"{released} Material(s) were released.")
    if warnings:
        lines.append(f"{len(warnings)} item(s) imported as declared - see the Logs tab.")
    if left_out:
        # Named here and not only in the log: what is missing from the tree is
        # the one thing nobody can see by looking at what was imported.
        lines.append("")
        lines.append(f"{len(left_out)} node(s) were left out of the import, at:")
        lines.extend("  " + note for note in left_out[:10])
        if len(left_out) > 10:
            lines.append(f"  ... and {len(left_out) - 10} more, in the Logs tab")
    unreviewed = [f for f in findings
                  if "reused " not in f and "released as " not in f and f not in left_out]
    if unreviewed:
        lines.append(f"{len(unreviewed)} left unset, picked automatically, or left out - "
                     "see the Logs tab.")
    lines += ["", result.get("identity", ""), result.get("note", "")]
    return "Import complete", "\n".join(line for line in lines if line is not None)


def failure(message: str, *, parse_seconds: float, import_seconds: float,
            done: int, total: int, journal: str | None) -> tuple[str, str]:
    """A title and a body for an import that stopped.

    It says what was reached, not only what broke: every completed Save stands,
    and the journal is what lets the run continue instead of starting again.
    """
    lines = [
        f"Stopped after {duration(import_seconds)}, {done} of {total} steps done.",
        f"Parsing took {duration(parse_seconds)}.",
        "",
        message.strip(),
        "",
        "Saves already made are not rolled back.",
    ]
    if journal:
        lines.append(f"Resume continues from the journal:\n{journal}")
    return "Import stopped", "\n".join(lines)
