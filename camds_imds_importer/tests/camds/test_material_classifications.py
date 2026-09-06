"""Classification support is driven by recorded evidence, not by guesswork."""
import pytest

from camds_imds_importer.camds.import_plan import ImportRequest
from camds_imds_importer.camds.material_classifications import (
    classification_code, describe, known_codes, require_supported, sort_key)
from camds_imds_importer.camds.operations import CreateRequest


@pytest.mark.parametrize("value, expected", [
    ("1.1.1", "1.1.1"),
    # IMDS reports carry the description; the wizard cell is the bare code.
    ("7.2: Ceramics / glass", "7.2"),
    ("1.1.1: unalloyed, low alloyed", "1.1.1"),
    ("  5.4.3 : Other duromers", "5.4.3"),
    ("5.1.a", "5.1.a"),
    # Never truncate to a shorter, different classification.
    ("5.1.x.9", None),
    ("Elastomers", None),
    ("", None),
    (None, None),
])
def test_code_is_extracted_without_guessing(value, expected):
    assert classification_code(value) == expected


def test_recorded_wizard_codes_are_allowed_and_absent_ones_are_not():
    codes = known_codes()
    for code in ("1.1.1", "1.1.2", "3.1", "5.3", "5.4.3", "6.1", "6.2", "7.2", "5.1.a"):
        assert code in codes, f"{code} was recorded in the wizard"
    # CAMDS lists no group 8 at all.
    assert "8.4" not in codes
    assert describe("7.2")


def test_full_report_classification_reaches_create_unchanged():
    request = CreateRequest("Material", "Ceramic ring", classification="7.2: Ceramics / glass")
    request.validate()
    assert require_supported(request.classification) == "7.2"


@pytest.mark.parametrize("value, match", [
    ("", "required"),
    ("Elastomers", "not a recognisable code"),
    ("8.4", "was not seen in the CAMDS creation wizard"),
])
def test_unsupported_classifications_fail_closed(value, match):
    with pytest.raises(ValueError, match=match):
        CreateRequest("Material", "x", classification=value).validate()


def test_sort_places_letter_suffixes_after_their_parent():
    assert sorted(["5.2", "5.1.b", "1.1.10", "5.1", "1.1.2", "5.1.a"], key=sort_key) == [
        "1.1.2", "1.1.10", "5.1", "5.1.a", "5.1.b", "5.2"]


def material_tree(classification):
    substance = {"uid": "s", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
                 "percentage": 100, "children": []}
    return {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": classification,
            "weight_g": 5, "children": [substance]}


def test_preflight_now_accepts_a_report_classification_other_than_1_1_1():
    ImportRequest(material_tree("7.2: Ceramics / glass")).validate()
    ImportRequest(material_tree("1.1.1: unalloyed, low alloyed")).validate()


def test_preflight_still_blocks_a_classification_never_seen_in_the_wizard():
    with pytest.raises(ValueError, match="was not seen in the CAMDS creation wizard"):
        ImportRequest(material_tree("8.4: Invented")).validate()
    with pytest.raises(ValueError, match="was not seen"):
        ImportRequest(material_tree(None)).validate()


def test_each_problem_is_reported_on_its_own_line():
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 2, "children": [
        material_tree("8.4"), dict(material_tree("8.5"), uid="m2", name="Other")]}
    with pytest.raises(ValueError) as info:
        ImportRequest(root).validate()
    lines = str(info.value).splitlines()
    # A message containing its own punctuation must not be split into fragments.
    assert lines[0].startswith("Import blocked before CAMDS changes (")
    assert sum("was not seen in the CAMDS creation wizard" in line for line in lines) == 2
