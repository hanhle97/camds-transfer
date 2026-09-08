"""Operator-chosen CAMDS entries for substances the catalogue does not settle.

Most substances resolve on their own: one CAMDS row carries the CAS, or one
carries the name. Some do not, and no rule decides them honestly:

* two rows share a CAS under different names - `9009-54-5` is offered as both
  "PUR" and "polyurethane foam";
* two rows share a name, one with a CAS and one without - "Epoxy resin" is
  offered as `61788-97-4` and as an entry with none;
* the report names something the catalogue does not - "PESTS" against
  "Polyester Stryrene Copolymer", "PPS" against "Polyphenylene sulfide";
* IMDS printed a truncated name, so the full one is not in the report at all -
  "...in combination with antimony com".

Picking one of those is a statement about what a material is made of. By
instruction the import takes the first row CAMDS offers rather than stopping,
and every such pick is written here as `"source": "first-row"` together with
the rows it chose between, and reported with the run. An operator's answer is
`"source": "operator"`, and a software pick never overwrites one.

The distinction is the point: an unreviewed pick that reads as approved is
worse than no record at all.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .import_plan import real_cas

DEFAULT_PATH = Path("config") / "substance_mapping.json"

NOTE = ("CAMDS substance choices. \"source\" says who chose: \"operator\" is a person, "
        "\"first-row\" is the software taking the first row CAMDS offered because the "
        "catalogue did not identify one. Replace any \"first-row\" id you disagree with and "
        "set \"source\" to \"operator\"; an entry with \"csid\": null is still unanswered.")


def key(node) -> str:
    """What the lookup was made on, which is what the choice belongs to."""
    cas = real_cas(node)
    return f"cas:{cas}" if cas else "name:" + str(node.get("name") or "").strip().casefold()


class SubstanceMapping:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self.entries: dict = {}
        if path.is_file():
            self.entries = json.loads(path.read_text(encoding="utf-8")).get("entries", {})

    def chosen(self, node) -> str | None:
        """The CAMDS `csid` a person picked for this substance, if any."""
        entry = self.entries.get(key(node))
        csid = (entry or {}).get("csid")
        return str(csid) if csid else None

    def answer(self, node) -> dict | None:
        """The whole recorded entry, so a caller can tell who chose it.

        `chosen` says which CAMDS entry; this says whether a person said so.
        The difference decides what to do when CAMDS's search no longer offers
        that entry: the software's own pick is made again, a person's stands.
        """
        entry = self.entries.get(key(node))
        return entry if entry and entry.get("csid") else None

    def auto(self, node, picked, rows) -> None:
        """Record a row the software picked because the catalogue was unclear.

        Kept apart from an operator's answer by `source`, and never allowed to
        overwrite one: how a pairing was arrived at does not change by being
        used again, and a software pick must never come to look approved.
        """
        entry = self.entries.get(key(node))
        if entry and entry.get("csid"):
            return
        self.entries[key(node)] = {
            "substance": node.get("name"),
            "cas": real_cas(node),
            "csid": str(picked.get("csid")),
            "chose": picked.get("enName"),
            "source": "first-row",
            "candidates": rows,
            "asked_at": datetime.now(timezone.utc).isoformat(),
        }
        # Written as it happens: a run that fails later must still leave behind
        # what it decided, and what it decided between.
        self.save()

    def ask(self, node, rows) -> None:
        """Record an unresolved substance and what CAMDS offered for it.

        Written where the answer goes, so the choice is made next to the
        evidence for it rather than from a log line.
        """
        entry = self.entries.get(key(node))
        if entry and entry.get("csid"):
            return  # already answered; never overwrite a decision
        self.entries[key(node)] = {
            "substance": node.get("name"),
            "cas": real_cas(node),
            "csid": None,
            "source": "unanswered",
            "candidates": rows,
            "asked_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    @property
    def unanswered(self) -> list[str]:
        return sorted(name for name, entry in self.entries.items() if not entry.get("csid"))

    @property
    def automatic(self) -> list[str]:
        """Entries the software chose. Nobody has confirmed these."""
        return sorted(name for name, entry in self.entries.items()
                      if entry.get("source") == "first-row")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"note": NOTE, "entries": self.entries}, indent=2, ensure_ascii=False),
            encoding="utf-8")
