"""Applications are matched by wording, recorded once, and never guessed.

CAMDS option codes are not IMDS codes: in this project's own report IMDS 63 on
Glass reads "listed under 10(b), 10(c) and 10(d)" while CAMDS 63 on Lead reads
"8(g)(ii-ii): single die 300 mm2 or larger". Only wording is compared.
"""
import json

import pytest

from camds_imds_importer.camds.application_mapping import ApplicationMapping, Resolution, key, normalise
from camds_imds_importer.camds.import_plan import ImportRequest
from camds_imds_importer.camds.tree_import import TreeImporter

# The real wording, straight from the report and the discovery document.
IMDS_NICKEL = "Other application (Surface not routinely touched or nickel release rate < 0.5µg/cm2/week) [33]"
CAMDS_NICKEL = [
    {"value": "37", "label": "Component of a surface likely to be routinely touched (eg. handles and "
                             "buckles), that have a nickel release rate exceeding 0.5μg/cm2/week."},
    {"value": "38", "label": "Other application (Surface not routinely touched or nickel release rate "
                             "< 0.5μg/cm2/week)"},
    {"value": "39", "label": "Not applicable"},
]


def substance(name="Nickel", application=IMDS_NICKEL, uid="s"):
    return {"uid": uid, "node_type": "SUBSTANCE", "name": name, "cas_number": "7440-02-0",
            "percentage": 100, "application_text": application, "application_id": "33", "children": []}


def tree(**substance_fields):
    child = substance(**substance_fields)
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 1.0, "children": [child]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Part", "weight_g": 1.0, "children": [material]}


def test_wording_matches_across_the_micro_sign_but_codes_do_not():
    # The only difference is U+00B5 MICRO SIGN against U+03BC GREEK SMALL MU.
    assert normalise(IMDS_NICKEL) == normalise(CAMDS_NICKEL[1]["label"])
    # The IMDS id is 33 and the CAMDS value is 38 for the very same statement.
    assert ApplicationMapping.match_by_name(IMDS_NICKEL, CAMDS_NICKEL).value == "38"


def test_a_trailing_imds_code_does_not_defeat_the_match():
    assert normalise("Not applicable [34]") == normalise("Not applicable")


@pytest.mark.parametrize("text, options", [
    # Truncated report wording matches nothing.
    ("listed under 10(b), 10(c) and 10(d).", CAMDS_NICKEL),
    # Two options with the same wording identify neither.
    ("Not applicable", [{"value": "1", "label": "Not applicable"}, {"value": "2", "label": "Not applicable"}]),
    ("", CAMDS_NICKEL),
])
def test_anything_but_a_single_exact_match_is_left_to_the_operator(text, options):
    assert ApplicationMapping.match_by_name(text, options) is None


def test_reviewed_pairings_survive_a_round_trip(tmp_path):
    mapping = ApplicationMapping(tmp_path / "application_mapping.json")
    assert mapping.resolve("Nickel", IMDS_NICKEL) is None
    mapping.record("Nickel", IMDS_NICKEL, Resolution("38", CAMDS_NICKEL[1]["label"], "exact-name-match"))
    mapping.save()

    reloaded = ApplicationMapping(tmp_path / "application_mapping.json")
    found = reloaded.resolve("Nickel", IMDS_NICKEL)
    assert found.value == "38" and found.source == "reviewed"
    stored = json.loads((tmp_path / "application_mapping.json").read_text(encoding="utf-8"))
    entry = next(iter(stored["entries"].values()))
    assert entry["imds_application"] == IMDS_NICKEL and entry["camds_value"] == "38"
    assert entry["recorded_at"]


def test_an_unmapped_application_is_reported_but_never_blocks(tmp_path):
    # The application comes from the parsed report; if it matches nothing CAMDS
    # offers it is left unset rather than refused or guessed.
    warnings = ImportRequest(tree()).validate(ApplicationMapping(tmp_path / "none.json"))
    assert any("left unset" in w for w in warnings)


def test_preflight_passes_once_the_pairing_is_reviewed(tmp_path):
    mapping = ApplicationMapping(tmp_path / "m.json")
    mapping.record("Nickel", IMDS_NICKEL, Resolution("38", CAMDS_NICKEL[1]["label"], "reviewed"))
    assert ImportRequest(tree()).validate(mapping) == []


def test_a_reviewed_pairing_needs_no_warning(tmp_path):
    mapping = ApplicationMapping(tmp_path / "m.json")
    mapping.record("Nickel", IMDS_NICKEL, Resolution("38", CAMDS_NICKEL[1]["label"], "reviewed"))
    assert ImportRequest(tree()).validate(mapping) == []


def test_a_material_level_application_is_left_unset_and_reported(tmp_path):
    """No CAMDS control exists for one, so it follows the rule every unplaceable
    application follows: left unset and said out loud, never guessed."""
    root = tree()
    root["children"][0]["application_text"] = "Some material application [12]"
    request = ImportRequest(root)
    warnings = request.validate(ApplicationMapping(tmp_path / "none.json"))
    assert any("no discovered CAMDS control" in w and "left unset" in w for w in warnings), warnings
    assert [n["uid"] for n in request.node_applications()] == ["m"]


async def test_an_unplaceable_application_is_reported_even_if_the_run_fails(tmp_path):
    """It is recorded before CAMDS is touched, so a later failure cannot hide it."""
    import json

    from camds_imds_importer.camds.tree_import import TreeImporter

    root = tree()
    root["children"][0]["application_text"] = "Some material application [12]"

    class Broken:
        reporter = None

        async def prepare(self):
            pass

        async def create_root(self, node, on_allocated=None):
            raise RuntimeError("CAMDS refused")

        async def can_reenter_saved(self):
            return False

        async def saved_children(self, path, at=(0, 1)):
            return []

        async def find_existing_component(self, node, resolved):
            return None

        async def find_existing_material(self, node):
            return None

        async def read_back_findings(self):
            return []

    importer = TreeImporter(Broken(), tmp_path)
    with pytest.raises(RuntimeError, match="CAMDS refused"):
        await importer.run(ImportRequest(root))
    assert any("has no CAMDS control" in note for note in importer.skipped), importer.skipped
    events = [json.loads(line) for line in
              next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert any(e["event"] == "node_application_skipped" and e["kind"] == "MATERIAL"
               for e in events), events


class ApplicationBrowser:
    """Fake editor exposing the Application tab behaviour from the discovery."""

    def __init__(self, options=None, cancelled=None):
        self.options = options if options is not None else CAMDS_NICKEL
        self.applied = []
        self.cancelled = [] if cancelled is None else cancelled
        self.reporter = None
        self.ref = None

    async def prepare(self):
        pass

    async def create_root(self, node, on_allocated=None):
        self.ref = ("CA_8_" + node["uid"], "0.01")
        if on_allocated:
            on_allocated(self.ref)
        return self.ref

    async def save(self):
        pass

    async def add_substance(self, name, node):
        return node["name"]

    async def can_reenter_saved(self):
        return False

    async def saved_children(self, path, at=(0, 1)):
        return []

    async def find_existing_component(self, node, resolved):
        return None

    async def find_existing_material(self, node):
        return None

    async def read_back_findings(self):
        return []

    async def open_saved(self, kind, ref):
        self.ref = ref

    async def value(self, label):
        return "Steel"

    async def identity(self):
        return self.ref

    async def verify_value(self, label, expected):
        pass

    async def verify_child_count(self, path, count, at=(0, 1)):
        pass

    async def verify_substance(self, path, node, at=(0, 1)):
        pass

    async def select(self, path, at=(0, 1)):
        if path[-1] == "Steel":
            self.ref = ("CA_8_m", "0.01")

    async def add_component(self, path, node, at=(0, 1), reuse_index=None):
        pass

    async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
        self.ref = ref
        return node["name"]

    async def application_options(self, substance_name):
        return f"dialog:{substance_name}", self.options

    async def apply_application(self, dialog, resolution):
        self.applied.append(resolution.value)

    async def cancel_application(self, dialog):
        self.cancelled.append(dialog)


async def test_exact_wording_resolves_and_the_pairing_is_recorded(tmp_path):
    mapping = ApplicationMapping(tmp_path / "m.json")
    backend = ApplicationBrowser()
    importer = TreeImporter(backend, tmp_path, mapping=mapping)
    await importer.run(ImportRequest(tree()))
    assert backend.applied == ["38"], "the CAMDS value, not the IMDS id"
    # The resolution is persisted, so the next run needs no matching at all.
    assert ApplicationMapping(tmp_path / "m.json").resolve("Nickel", IMDS_NICKEL).value == "38"
    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    applied = next(e for e in events if e["event"] == "application_applied")
    assert applied["camds_value"] == "38" and applied["source"] == "exact-name-match"
    assert applied["imds_application"] == IMDS_NICKEL


async def test_an_unmatched_application_is_skipped_and_the_import_continues(tmp_path):
    backend = ApplicationBrowser(options=[{"value": "1", "label": "Something else"}])
    importer = TreeImporter(backend, tmp_path, mapping=ApplicationMapping(tmp_path / "m.json"))
    result = await importer.run(ImportRequest(tree()))
    assert backend.applied == [], "nothing may be confirmed when the option is unknown"
    assert backend.cancelled, "the dialog is dismissed rather than left open"
    assert result["skipped"] and "left unset" in result["skipped"][0]
    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    skipped = next(e for e in events if e["event"] == "application_skipped")
    assert skipped["offered"] == ["Something else"]
    assert skipped["imds_application"] == IMDS_NICKEL


@pytest.mark.parametrize("stored, why", [
    # Approved against wording CAMDS no longer shows for that value.
    (Resolution("38", "Wording that has since changed", "reviewed"), "wording changed"),
    # Approved against an option that is no longer offered at all.
    (Resolution("99", "Gone", "reviewed"), "option withdrawn"),
])
async def test_a_stale_pairing_is_skipped_rather_than_applied(tmp_path, stored, why):
    mapping = ApplicationMapping(tmp_path / "m.json")
    mapping.record("Nickel", IMDS_NICKEL, stored)
    backend = ApplicationBrowser()
    result = await TreeImporter(backend, tmp_path, mapping=mapping).run(ImportRequest(tree()))
    assert backend.applied == [], why
    assert result["skipped"], why


def test_reusing_a_pairing_does_not_make_it_look_human_approved(tmp_path):
    """"reviewed" means a person signed off; a name match must never become one."""
    mapping = ApplicationMapping(tmp_path / "m.json")
    mapping.record("Iron", "Not applicable [34]",
                   Resolution("39", "Not applicable", "exact-name-match"))
    mapping.save()

    # A later run resolves it from the file and records it again.
    reloaded = ApplicationMapping(tmp_path / "m.json")
    found = reloaded.resolve("Iron", "Not applicable [34]")
    reloaded.record("Iron", "Not applicable [34]",
                    Resolution(found.value, found.label, found.source))
    reloaded.save()

    entry = next(iter(json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["entries"].values()))
    assert entry["source"] == "exact-name-match", "provenance must survive being used"


def test_using_a_pairing_again_does_not_restamp_it(tmp_path):
    """recorded_at says when the pairing was decided, not when it was last used.
    This file is in version control, and a timestamp rewritten on every run is
    noise that blocks a branch switch."""
    mapping = ApplicationMapping(tmp_path / "m.json")
    mapping.record("Nickel", IMDS_NICKEL, Resolution("38", "Other application", "exact-name-match"))
    first = mapping.entries[key("Nickel", IMDS_NICKEL)]["recorded_at"]

    mapping.record("Nickel", IMDS_NICKEL, Resolution("38", "Other application", "exact-name-match"))
    assert mapping.entries[key("Nickel", IMDS_NICKEL)]["recorded_at"] == first


def test_a_pairing_that_resolves_differently_is_a_new_decision(tmp_path):
    """Dated from a stored value rather than a second clock reading: two calls
    in the same run land in the same millisecond on Windows."""
    mapping = ApplicationMapping(tmp_path / "m.json")
    mapping.record("Nickel", IMDS_NICKEL, Resolution("38", "Other application", "exact-name-match"))
    mapping.entries[key("Nickel", IMDS_NICKEL)]["recorded_at"] = "2020-01-01T00:00:00+00:00"

    mapping.record("Nickel", IMDS_NICKEL, Resolution("39", "Not applicable", "exact-name-match"))
    entry = mapping.entries[key("Nickel", IMDS_NICKEL)]
    assert entry["camds_value"] == "39"
    assert entry["recorded_at"] != "2020-01-01T00:00:00+00:00", "a changed pairing is redated"

    mapping.record("Nickel", IMDS_NICKEL, Resolution("39", "Not applicable", "exact-name-match"))
    assert mapping.entries[key("Nickel", IMDS_NICKEL)]["recorded_at"] == entry["recorded_at"]
