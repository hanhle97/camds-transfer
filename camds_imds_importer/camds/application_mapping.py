"""Reviewed IMDS-to-CAMDS application mappings.

An application is a regulatory statement about a part, and CAMDS option codes
are not IMDS codes: in one report IMDS 63 on Glass reads "listed under 10(b),
10(c) and 10(d)" while CAMDS 63 on Lead reads "8(g)(ii-ii): single die 300 mm2
or larger". Copying the number across would declare the wrong exemption, so a
code is only ever used after a human has approved that pairing, or after the
option text matched exactly.

Nothing here guesses. Matching is exact after normalisation; anything else is
handed back to the operator.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path("config") / "application_mapping.json"


def normalise(text: str | None) -> str:
    """Compare option wording, not its punctuation.

    NFKC folds the micro sign into mu, which is the only difference between the
    IMDS and CAMDS wording of the nickel release-rate option.
    """
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    # Drop a trailing IMDS "[33]" and every non-alphanumeric character.
    folded = re.sub(r"\[\d+\]\s*$", "", folded)
    return re.sub(r"[^0-9a-z]+", "", folded)


def key(substance: str | None, application_text: str | None) -> str:
    return normalise(substance) + "|" + normalise(application_text)


@dataclass(frozen=True, slots=True)
class Resolution:
    """One CAMDS option chosen for one parsed application."""

    value: str
    label: str
    source: str  # "reviewed" or "exact-name-match"


class ApplicationMapping:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self.entries: dict = {}
        if path.is_file():
            self.entries = json.loads(path.read_text(encoding="utf-8")).get("entries", {})

    def resolve(self, substance: str | None, application_text: str | None) -> Resolution | None:
        entry = self.entries.get(key(substance, application_text))
        if not entry:
            return None
        return Resolution(entry["camds_value"], entry.get("camds_label", ""), "reviewed")

    @staticmethod
    def match_by_name(application_text: str | None, options: list[dict]) -> Resolution | None:
        """Return the single option whose wording matches, or None.

        Several matches are not a tie to break; they mean the wording does not
        identify one option, so the operator decides.
        """
        wanted = normalise(application_text)
        if not wanted:
            return None
        hits = [o for o in options if normalise(o.get("label")) == wanted]
        if len(hits) != 1:
            return None
        return Resolution(str(hits[0]["value"]), hits[0].get("label", ""), "exact-name-match")

    def record(self, substance: str | None, application_text: str | None, resolution: Resolution) -> None:
        """Persist a resolution so the next run is deterministic.

        How a pairing was first arrived at does not change by using it again:
        re-recording keeps the original provenance, so a match the software made
        never comes to look like one a person approved.
        """
        existing = self.entries.get(key(substance, application_text))
        if existing:
            resolution = Resolution(resolution.value, resolution.label, existing.get("source", resolution.source))
        self.entries[key(substance, application_text)] = {
            "substance": substance,
            "imds_application": application_text,
            "camds_value": resolution.value,
            "camds_label": resolution.label,
            "source": resolution.source,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "note": ("Reviewed IMDS to CAMDS application mappings. CAMDS option codes are not IMDS "
                     "codes; each entry pairs the wording seen in both systems."),
            "entries": self.entries,
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def missing(self, root: dict) -> list[tuple[str, str]]:
        """Every (substance, application) in a tree that has no reviewed entry."""
        found: dict[str, tuple[str, str]] = {}

        def visit(node):
            text = node.get("application_text")
            if node.get("node_type") == "SUBSTANCE" and (text or node.get("application_id")):
                if not self.resolve(node.get("name"), text):
                    found.setdefault(key(node.get("name"), text), (node.get("name") or "", text or ""))
            for child in node.get("children", []):
                visit(child)

        visit(root)
        return sorted(found.values())
