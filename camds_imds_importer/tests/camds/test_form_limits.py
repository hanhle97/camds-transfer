"""Length limits read from the browser DOM, and what they may block.

100 for a name, 50 for a number, 50 for the substance search box: all three
were read from form attributes and, as AUTHENTICATED_DISCOVERY.md records,
never tested against the server. The JSON API does not go through those forms,
so applying them there blocked real IMDS data on a browser's rule.

By decision they are reported, not refused. What makes that safe is that a
truncation cannot pass silently: every name and number is read back and
compared in full, and a substance that does not resolve exactly still stops
the run.
"""
import pytest

from camds_imds_importer.camds.api import CamdsApiError
from camds_imds_importer.camds.api_backend import ApiBackend
from camds_imds_importer.camds.import_plan import ImportRequest

LONG_NAME = ("Coating film inorg./org PUR/PE (Sealant inorganic/organic with content of "
             "Polyurethan and PE-lubricant)")
LONG_SUBSTANCE = ("ISO 1043-4 FR(17) aromatic brominated compounds (excluding brominated "
                  "diphenyl ether and biphenyls) in combination with antimony com")


def substance(**extra):
    return {"uid": "s", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
            "percentage": 100, "children": [], **extra}


def tree(**material):
    return {"uid": "r", "node_type": "COMPONENT", "name": "Assembly", "weight_g": 10.0,
            "children": [{"uid": "m", "node_type": "MATERIAL", "name": "Steel",
                          "classification": "1.1.1", "weight_g": 1.0,
                          "children": [substance()], **material}]}


def test_a_name_longer_than_the_form_allows_is_reported_not_refused():
    assert len(LONG_NAME) > 100
    warnings = ImportRequest(tree(name=LONG_NAME)).validate()
    assert any(str(len(LONG_NAME)) in w and "100" in w for w in warnings), warnings


def test_a_number_longer_than_the_form_allows_is_reported_not_refused():
    warnings = ImportRequest(tree(material_number="N" * 60)).validate()
    assert any("60 characters" in w for w in warnings), warnings


def test_a_substance_without_a_cas_is_looked_up_by_its_full_name():
    assert len(LONG_SUBSTANCE) > 50
    root = tree()
    root["children"][0]["children"] = [substance(cas_number=None, name=LONG_SUBSTANCE)]
    warnings = ImportRequest(root).validate()
    assert any("search box accepts 50" in w for w in warnings), warnings


def test_a_substance_with_neither_a_cas_nor_a_name_is_still_refused():
    root = tree()
    root["children"][0]["children"] = [substance(cas_number=None, name=" ")]
    with pytest.raises(ValueError, match="name required"):
        ImportRequest(root).validate()


async def test_a_name_camds_shortened_fails_read_back():
    """The check that makes reporting safe instead of reckless."""
    backend = ApiBackend(object())
    backend.view = {"data": {"cname": LONG_NAME[:100]}, "structureVO": {}}
    await backend.verify_value("Material Name", LONG_NAME[:100])
    with pytest.raises(CamdsApiError, match="Read-back mismatch"):
        await backend.verify_value("Material Name", LONG_NAME)
