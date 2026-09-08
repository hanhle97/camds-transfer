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
    with pytest.raises(ValueError, match="choose one in the Classification column"):
        ImportRequest(material_tree("8.4: Invented")).validate()
    with pytest.raises(ValueError, match="'missing'"):
        ImportRequest(material_tree(None)).validate()


def test_each_problem_is_reported_on_its_own_line():
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 2, "children": [
        material_tree("8.4"), dict(material_tree("8.5"), uid="m2", name="Other")]}
    with pytest.raises(ValueError) as info:
        ImportRequest(root).validate()
    lines = str(info.value).splitlines()
    # A message containing its own punctuation must not be split into fragments.
    assert lines[0].startswith("Import blocked before CAMDS changes (")
    assert sum("choose one in the Classification column" in line for line in lines) == 2


def test_one_rule_decides_whether_a_class_has_to_be_chosen():
    """The preflight, the table and the snapshot ask the same question."""
    from camds_imds_importer.camds.material_classifications import needs_choice

    assert needs_choice(None) and needs_choice("") and needs_choice("   ")
    assert needs_choice("8.4"), "a code the wizard was never seen to offer"
    assert needs_choice("paper"), "not a code at all"
    assert not needs_choice("1.1.1")
    assert not needs_choice("5.1.b: unfilled Thermoplastics")


def test_a_chosen_classification_is_written_onto_the_material():
    """What the operator picked has to reach the wizard, the validation and the
    fingerprint as one classification, or they would disagree about it."""
    plain = ImportRequest(material_tree(None))
    chosen = ImportRequest(material_tree(None), {}, {"m": "5.1.b"}).snapshot()
    chosen.validate()
    assert chosen.materials()[0]["classification"] == "5.1.b"
    assert chosen.chosen == ["Steel: IMDS printed no classification, "
                            "so 5.1.b: unfilled Thermoplastics was chosen"]
    assert chosen.fingerprint != plain.snapshot().fingerprint, "a different run"


def test_a_choice_cannot_overwrite_a_classification_the_report_stated():
    """It is the supplier's statement about their own material."""
    request = ImportRequest(material_tree("7.2: Ceramics / glass"), {}, {"m": "5.1.b"}).snapshot()
    assert request.materials()[0]["classification"] == "7.2: Ceramics / glass"
    assert request.chosen == []


def test_a_chosen_classification_is_checked_like_any_other():
    """The table only offers recorded codes; the rule still lives in one place."""
    with pytest.raises(ValueError, match="choose one in the Classification column"):
        ImportRequest(material_tree(None), {}, {"m": "8.4"}).snapshot().validate()
