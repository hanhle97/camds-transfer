"""Material classifications the CAMDS creation wizard was observed to offer.

The allowlist is evidence, not a guess: `material_classifications.json` is
generated from a recorded session of the "Creation of a new material" dialog,
whose first column holds the bare code. A classification that was never seen in
that dialog still fails closed.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

EVIDENCE = Path(__file__).with_name("material_classifications.json")
# Created and saved end to end against CAMDS; see SAVE_TEST_55095125.md.
VERIFIED = frozenset({"1.1.1"})

# The code must stand alone or be followed by ": description". Truncating a
# longer code would silently pick a different class. CAMDS really does
# publish letter-suffixed codes such as 5.1.a and 5.1.b.
_CODE = re.compile(r"^\s*(\d+(?:\.\d+)*(?:\.[A-Za-z])?)\s*(?::|$)")


@lru_cache(maxsize=1)
def _evidence() -> dict:
    if not EVIDENCE.is_file():
        return {}
    return json.loads(EVIDENCE.read_text(encoding="utf-8")).get("classifications", {})


def known_codes() -> frozenset[str]:
    return frozenset(_evidence()) | VERIFIED


def sort_key(code: str):
    """Order 1, 1.1, 1.1.1, 5.1, 5.1.a - letter suffixes sort after their parent."""
    return tuple((int(part), "") if part.isdigit() else (0, part) for part in code.split("."))


def describe(code: str) -> str:
    return _evidence().get(code, "")


def classification_code(value: str | None) -> str | None:
    """Reduce a parsed classification to the bare code the wizard lists.

    IMDS reports carry "7.2: Ceramics / glass"; the wizard's selectable cell is
    "7.2". Comparing the full string against a code is why real 1.1.1 materials
    ("1.1.1: unalloyed, low alloyed") were rejected as unsupported.
    """
    if not value:
        return None
    match = _CODE.match(str(value))
    return match.group(1) if match else None


def needs_choice(value: str | None) -> bool:
    """Whether the operator has to say what this Material is.

    One rule, asked in three places: the preflight that blocks the import, the
    table that offers the choice, and the snapshot that applies it. Two copies
    of it would drift, and the drift would either offer a choice that changes
    nothing or block on something already chosen.
    """
    return classification_code(value) not in known_codes()


def require_supported(value: str | None) -> str:
    if not value or not str(value).strip():
        raise ValueError("Material classification is required")
    code = classification_code(value)
    if code is None:
        raise ValueError(f"Material classification {value!r} is not a recognisable code such as 5.3 or '5.3: Elastomers'")
    if code not in known_codes():
        raise ValueError(
            f"Material classification {code} was not seen in the CAMDS creation wizard; "
            "re-record it with 'Record classification wizard', or map an existing CAMDS Material instead")
    return code
