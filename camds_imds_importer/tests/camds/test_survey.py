"""Asking CAMDS what it already holds, for a whole report at once.

The panel took one name and answered with one page of rows. The question before
an import is plural: which of these 52 Materials does CAMDS already have?
"""
import pytest

from camds_imds_importer.camds.survey import (
    Lookup, components, materials, note, pasted, released, summarise)


def node(kind, name, number=None, children=()):
    key = "material_number" if kind == "MATERIAL" else "part_number"
    return {"node_type": kind, "name": name, key: number, "children": list(children)}


def test_a_material_is_asked_about_once_however_often_it_appears():
    """"Ep-Ni" appears three times in one report and is one question."""
    root = node("COMPONENT", "Part", "1", [
        node("MATERIAL", "Ep-Ni"), node("MATERIAL", "Ep-Ni"),
        node("COMPONENT", "Sub", "2", [node("MATERIAL", "Ep-Ni"), node("MATERIAL", "Cu99")])])
    assert [m.name for m in materials(root)] == ["Ep-Ni", "Cu99"]


def test_two_materials_sharing_a_name_but_not_a_number_are_two_questions():
    root = node("COMPONENT", "Part", "1", [
        node("MATERIAL", "Ep-Ni", "A1"), node("MATERIAL", "Ep-Ni", "B2")])
    assert [(m.name, m.number) for m in materials(root)] == [("Ep-Ni", "A1"), ("Ep-Ni", "B2")]


def test_only_components_with_a_ten_digit_number_are_looked_up():
    """A part number of another shape is not a CAMDS component number, and
    looking it up would answer about whatever else contains those characters."""
    root = node("COMPONENT", "Top", "1274478538", [
        node("COMPONENT", "Short", "5000.000.399"),
        node("COMPONENT", "None at all", None),
        node("COMPONENT", "Eleven", "12744785381"),
        node("COMPONENT", "Good", "1274478539")])
    assert [c.number for c in components(root)] == ["1274478538", "1274478539"]


def test_the_same_component_number_twice_is_one_question():
    root = node("COMPONENT", "Top", "1274478538", [node("COMPONENT", "Again", "1274478538")])
    assert len(components(root)) == 1


@pytest.mark.parametrize("text, numbers", [
    ("1274478538\n1274478539", ["1274478538", "1274478539"]),
    ("1274478538, 1274478539", ["1274478538", "1274478539"]),
    ("  1274478538  ", ["1274478538"]),
    ("1274478538\r\n1274478538", ["1274478538"]),
    ("", []),
])
def test_a_pasted_list_takes_whatever_shape_the_operator_had_it_in(text, numbers):
    """A spreadsheet column, a mail, commas: everything that is not a digit
    separates, so no format has to be explained."""
    assert [item.number for item in pasted(text)] == numbers


def test_only_released_versions_count():
    """0.01 is a draft. Counting one says CAMDS holds a Material when what it
    holds is an abandoned attempt at it."""
    rows = [{"mdsId": "a", "version": "0.01", "createDate": "2026-09-01"},
            {"mdsId": "b", "version": "2", "createDate": "2026-08-01"},
            {"mdsId": "c", "version": "1", "createDate": "2026-09-05"}]
    assert [r["mdsId"] for r in released(rows)] == ["c", "b"], "newest first"


def test_a_loose_search_hit_is_counted_apart_from_a_match():
    """"EPDM" answers with "TPV - (EPDM + PP)" as well, and that is not it."""
    rows = [{"mdsId": "CA_8_1", "mdsName": "EPDM", "version": "1", "createDate": "2026-08-09"},
            {"mdsId": "CA_8_2", "mdsName": "TPV - (EPDM + PP)", "version": "1",
             "createDate": "2026-08-14"}]
    found = summarise(Lookup(name="EPDM"), rows)
    assert found.matches == 1 and found.others == 1
    assert found.mds_id == "CA_8_1"


def test_a_number_decides_when_there_is_one():
    rows = [{"mdsId": "CA_5_1", "mdsName": "Something else", "symbol": "1274478538",
             "version": "2", "createDate": "2026-05-07"}]
    found = summarise(Lookup(name="Bracket", number="1274478538"), rows)
    assert found.matches == 1 and found.mds_id == "CA_5_1" and found.version == "2"


def test_the_newest_released_version_is_the_one_reported():
    rows = [{"mdsId": "old", "mdsName": "Cu99", "version": "1", "createDate": "2025-01-01"},
            {"mdsId": "new", "mdsName": "Cu99", "version": "3", "createDate": "2026-01-01"}]
    found = summarise(Lookup(name="Cu99"), rows)
    assert found.mds_id == "new" and found.created == "2026-01-01"


def test_nothing_found_says_so_rather_than_leaving_the_row_blank():
    found = summarise(Lookup(name="Cu99"), [])
    assert found.matches == 0
    assert found.columns[2] == "not found"
    assert all(cell for cell in found.columns), "no empty cell to puzzle over"


def test_the_summary_line_counts_both_answers():
    rows = [summarise(Lookup(name="a"), [{"mdsId": "1", "mdsName": "a", "version": "1"}]),
            summarise(Lookup(name="b"), [])]
    assert note(rows, "Material") == (
        "1 of 2 Material(s) are already in CAMDS as a released version; "
        "1 are not. Nothing was created.")
    assert note([], "Component") == "No Component to look up."
