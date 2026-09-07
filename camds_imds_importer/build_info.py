"""Which build is running.

"Rebuild and try again" is not something an operator can verify, and a stale
executable answered three runs in a row with a defect that had already been
fixed in the source. The application says which build it is, so the question
has an answer instead of an assurance.
"""
from __future__ import annotations

import sys
from pathlib import Path

STAMP = Path(__file__).with_name("build_stamp.txt")


def build_stamp() -> str:
    """The commit and time this executable was built from, or the source tree."""
    if STAMP.is_file():
        # lstrip of the byte-order mark: a stamp written by an older build.ps1
        # carries one, and it shows up in front of the commit on screen.
        text = STAMP.read_text(encoding="utf-8").lstrip("﻿").strip()
        if text:
            return text
    if getattr(sys, "frozen", False):
        return "built without a stamp - rebuild with build.ps1 to get one"
    return "running from the source tree"
