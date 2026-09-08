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
    # Longer than the search box takes, but the report printed the whole of it.
    name = LONG_SUBSTANCE[:120]
    assert 50 < len(name) < 132
    root = tree()
    root["children"][0]["children"] = [substance(cas_number=None, name=name)]
    warnings = ImportRequest(root).validate()
    assert any("search box accepts 50" in w for w in warnings), warnings


def test_a_name_imds_itself_cut_short_is_reported_as_the_worse_problem():
    """132 characters is IMDS's own cut, not a form's: the name the lookup has
    to work with is not the substance's whole name."""
    assert len(LONG_SUBSTANCE) == 132
    root = tree()
    root["children"][0]["children"] = [substance(cas_number=None, name=LONG_SUBSTANCE)]
    warnings = ImportRequest(root).validate()
    assert any("cut this name at 132 characters" in w for w in warnings), warnings
    assert not any("search box accepts 50" in w for w in warnings),         "the length of the box is beside the point when the name itself is incomplete"


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


def test_the_catalogue_check_visits_each_lookup_once():
    """5764 Substance nodes in the real tree are about 200 distinct searches, and
    checking those few is what makes a read-only preflight possible at all."""
    root = tree()
    material = root["children"][0]
    material["children"] = [
        substance(uid="a", cas_number="7439-89-6"),
        substance(uid="b", cas_number="7439-89-6", name="Iron, again"),   # same CAS
        substance(uid="c", cas_number=None, name="Misc., not to declare"),
        substance(uid="d", cas_number=None, name="Misc., not to declare"),  # same name
        substance(uid="e", cas_number="7440-50-8", name="Copper"),
    ]
    lookups = ImportRequest(root).substance_lookups()
    assert [n["uid"] for n in lookups] == ["a", "c", "e"]


def test_a_mapped_material_is_not_looked_up():
    """Its composition is not written, so its substances are never searched."""
    root = tree()
    request = ImportRequest(root, {"m": ("CA_8_1", "0.01")})
    assert request.substance_lookups() == []
