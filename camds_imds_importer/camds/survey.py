"""Ask CAMDS what it already holds, for a whole report at once.

The search panel took one name and answered with one page of rows. The question
an operator actually has before an import is different and plural: *which of
these does CAMDS already have?* - 52 Materials, or every Component the report
names, or a list somebody pasted from a spreadsheet.

Only whole-numbered versions are counted. 1, 2 and 6 are released; 0.01 is a
draft somebody left half-built, and counting those would report a Material as
present when only an abandoned attempt at it is.

Nothing here talks to CAMDS. It decides what to look up and what the answers
mean, so both can be tested without a session.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A CAMDS component number as the reports carry it. Ten digits, nothing else:
# a part number of another shape is not one, and looking it up would answer
# about whatever else happens to contain those characters.
COMPONENT_NUMBER = re.compile(r"^\d{10}$")


@dataclass(frozen=True, slots=True)
class Lookup:
    """One thing to ask CAMDS about."""

    name: str = ""
    number: str = ""

    @property
    def label(self) -> str:
        return self.name or self.number


@dataclass
class Found:
    """What CAMDS answered about one lookup."""

    lookup: Lookup
    matches: int = 0
    mds_id: str = ""
    version: str = ""
    created: str = ""
    others: int = 0          # released rows the search returned that are not this one
    columns: list = field(default_factory=list)


HEADINGS = ("Looked for", "Number", "In CAMDS", "Version", "Matches", "Newest created")


def materials(root: dict) -> list[Lookup]:
    """Every Material the report declares, once each.

    The same Material appears many times in a tree - "Ep-Ni" three times in one
    report - and asking about it three times answers the same thing three times.
    """
    seen, found = set(), []

    def visit(node):
        if node.get("node_type") == "MATERIAL":
            item = Lookup(str(node.get("name") or "").strip(),
                          str(node.get("material_number") or "").strip())
            if item.label and (item.name.casefold(), item.number) not in seen:
                seen.add((item.name.casefold(), item.number))
                found.append(item)
        for child in node.get("children") or []:
            visit(child)

    visit(root)
    return found


def components(root: dict) -> list[Lookup]:
    """Every Component carrying a ten-digit CAMDS number.

    A Component without one cannot be looked up by number, and its name is not
    an identity, so it is left out rather than answered about vaguely.
    """
    seen, found = set(), []

    def visit(node):
        if node.get("node_type") == "COMPONENT":
            number = str(node.get("part_number") or "").strip()
            if COMPONENT_NUMBER.match(number) and number not in seen:
                seen.add(number)
                found.append(Lookup(str(node.get("name") or "").strip(), number))
        for child in node.get("children") or []:
            visit(child)

    visit(root)
    return found


def pasted(text: str) -> list[Lookup]:
    """Component numbers pasted from wherever the operator had them.

    A spreadsheet column, a mail, a list with commas: everything that is not a
    digit separates, so no particular format has to be explained.
    """
    seen, found = set(), []
    for number in re.split(r"[^0-9]+", text or ""):
        if number and number not in seen:
            seen.add(number)
            found.append(Lookup(number=number))
    return found


def released(rows) -> list[dict]:
    """Rows for a released version, newest first.

    0.01 is a draft. Reporting one as a match would say CAMDS holds a Material
    when what it holds is an abandoned attempt at it.
    """
    whole = [row for row in rows
             if str(row.get("version") or "").strip().isdigit() and row.get("mdsId")]
    return sorted(whole, key=lambda row: str(row.get("createDate") or ""), reverse=True)


def summarise(lookup: Lookup, rows) -> Found:
    """What CAMDS's answer means for one lookup.

    The search matches loosely - "EPDM" answers with "TPV - (EPDM + PP)" too -
    so a match is a row whose number equals the one asked for, or whose name
    equals it exactly when there is no number. The rest are counted separately
    rather than dropped: they are what an operator would want to look at when
    the answer is none.
    """
    candidates = released(rows)
    if lookup.number:
        exact = [r for r in candidates if str(r.get("symbol") or "").strip() == lookup.number]
    else:
        wanted = lookup.name.casefold()
        exact = [r for r in candidates
                 if str(r.get("mdsName") or "").strip().casefold() == wanted]
    best = exact[0] if exact else None
    return Found(
        lookup=lookup,
        matches=len(exact),
        mds_id=str(best.get("mdsId")) if best else "",
        version=str(best.get("version")) if best else "",
        created=str(best.get("createDate") or "") if best else "",
        others=len(candidates) - len(exact),
        columns=[lookup.name or "-", lookup.number or "-",
                 str(best.get("mdsId")) if best else "not found",
                 str(best.get("version")) if best else "-",
                 str(len(exact)),
                 str(best.get("createDate") or "-") if best else "-"],
    )


def note(found: list[Found], kind: str) -> str:
    """One line for the status bar, saying what the table now shows."""
    present = sum(1 for row in found if row.matches)
    if not found:
        return f"No {kind} to look up."
    return (f"{present} of {len(found)} {kind}(s) are already in CAMDS as a released "
            f"version; {len(found) - present} are not. Nothing was created.")
