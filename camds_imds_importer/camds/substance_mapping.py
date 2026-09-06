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

Picking one of those is a statement about what a material is made of, so a
person makes it once and it is recorded here with what they were choosing
between. Nothing in this file is inferred, and an entry without a chosen id is
a question, not an answer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .import_plan import real_cas

DEFAULT_PATH = Path("config") / "substance_mapping.json"

NOTE = ("CAMDS substance choices. An entry with \"csid\": null is unanswered: put the id "
        "of the intended row from \"candidates\" there. Only a person decides these.")


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
            "candidates": rows,
            "asked_at": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def unanswered(self) -> list[str]:
        return sorted(name for name, entry in self.entries.items() if not entry.get("csid"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"note": NOTE, "entries": self.entries}, indent=2, ensure_ascii=False),
            encoding="utf-8")
