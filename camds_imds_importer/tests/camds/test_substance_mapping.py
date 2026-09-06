"""Substances the catalogue does not settle on its own.

A live check of the real 8052-node tree left 13 of 204 lookups unresolved.
Two of them the report itself answers: several CAMDS rows share a CAS, and one
of them carries the name the report uses. The other eleven are genuine
questions - "PESTS" against "Polyester Stryrene Copolymer", a CAS offered as
both "PUR" and "polyurethane foam" - and a person answers those.
"""
import json

import pytest

from camds_imds_importer.camds.api import CamdsApi, CamdsApiError
from camds_imds_importer.camds.api_backend import ApiBackend
from camds_imds_importer.camds.substance_mapping import SubstanceMapping


class Catalogue:
    """Answers findSubstanceByCondition with the rows CAMDS really offered."""

    def __init__(self, rows):
        self.rows = rows

    async def post(self, url, params=None, data=None, headers=None):
        class Response:
            status = 200

            @staticmethod
            async def json():
                return {"respCode": "0", "ok": True, "data": {"records": self.rows}}
        return Response


def backend(rows, mapping, first_row=True):
    return ApiBackend(CamdsApi(Catalogue(rows), base_url="https://camds.test"), mapping,
                      first_row_when_unclear=first_row)


def substance(name, cas=None):
    return {"uid": "s", "node_type": "SUBSTANCE", "name": name, "cas_number": cas,
            "percentage": 1, "children": []}


SILICON = [{"csid": "7812", "cas": "7440-21-3", "enName": "P-SI"},
           {"csid": "2971", "cas": "7440-21-3", "enName": "Silicon"}]
POLYURETHANE = [{"csid": "8066", "cas": "9009-54-5", "enName": "PUR"},
                {"csid": "3449", "cas": "9009-54-5", "enName": "polyurethane foam"}]


async def test_the_report_settles_a_shared_cas_when_it_names_one_of_the_rows(tmp_path):
    """Not a preference of ours: the report calls it Silicon, and CAMDS offers
    a row called Silicon under that CAS."""
    made = backend(SILICON, SubstanceMapping(tmp_path / "m.json"))
    assert (await made.resolve_substance(substance("Silicon", "7440-21-3")))["csid"] == "2971"
    assert made.pending == [], "nothing to ask about"


async def test_an_unclear_catalogue_takes_the_first_row_and_says_so(tmp_path):
    """By instruction: do not stop. "Polyurethane" is neither "PUR" nor
    "polyurethane foam", so the pick is the software's and is reported."""
    mapping = SubstanceMapping(tmp_path / "m.json")
    made = backend(POLYURETHANE, mapping)
    found = await made.resolve_substance(substance("Polyurethane", "9009-54-5"))
    assert found["csid"] == "8066", "the first row CAMDS offered"
    assert made.findings and "PUR" in made.findings[0] and "9009-54-5" in made.findings[0]
    assert mapping.automatic == ["cas:9009-54-5"]
    assert mapping.unanswered == [], "it was answered, just not by a person"


async def test_a_software_pick_is_never_recorded_as_an_operator_answer(tmp_path):
    """An unreviewed pick that reads as approved is worse than no record."""
    mapping = SubstanceMapping(tmp_path / "m.json")
    await backend(POLYURETHANE, mapping).resolve_substance(
        substance("Polyurethane", "9009-54-5"))
    mapping.save()
    entry = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["entries"]["cas:9009-54-5"]
    assert entry["source"] == "first-row"
    assert entry["chose"] == "PUR"
    assert [r["enName"] for r in entry["candidates"]] == ["PUR", "polyurethane foam"]


async def test_a_software_pick_never_replaces_an_operator_answer(tmp_path):
    mapping = SubstanceMapping(tmp_path / "m.json")
    mapping.entries["cas:9009-54-5"] = {"csid": "3449", "source": "operator"}
    found = await backend(POLYURETHANE, mapping).resolve_substance(
        substance("Polyurethane", "9009-54-5"))
    assert found["csid"] == "3449", "the person's choice wins"
    assert mapping.entries["cas:9009-54-5"]["source"] == "operator"


async def test_nothing_offered_at_all_still_stops_the_run(tmp_path):
    """There is no first row to take. The truncated ISO 1043-4 name is this."""
    made = backend([], SubstanceMapping(tmp_path / "m.json"))
    with pytest.raises(CamdsApiError, match="no rows at all"):
        await made.resolve_substance(substance("ISO 1043-4 FR(17) aromatic brominated"))
    assert [n["name"] for n, _ in made.pending] == ["ISO 1043-4 FR(17) aromatic brominated"]


async def test_the_strict_rule_still_exists_and_refuses(tmp_path):
    """Turning the instruction off must restore stopping, not soften it."""
    made = backend(POLYURETHANE, SubstanceMapping(tmp_path / "m.json"), first_row=False)
    with pytest.raises(CamdsApiError, match="matched 2 entries"):
        await made.resolve_substance(substance("Polyurethane", "9009-54-5"))


async def test_whitespace_is_not_normalised_away_when_it_tells_rows_apart(tmp_path):
    """CAMDS offers this CAS three times, two with a doubled space. Folding that
    would make all three match and lose the one the report names."""
    rows = [{"csid": "10386", "cas": "182442-95-1", "enName": "Cobalt lithium manganese  nickel oxide"},
            {"csid": "13247", "cas": "182442-95-1", "enName": "Cobalt lithium manganese  nickel oxide"},
            {"csid": "13289", "cas": "182442-95-1", "enName": "Cobalt lithium manganese nickel oxide"}]
    made = backend(rows, SubstanceMapping(tmp_path / "m.json"))
    found = await made.resolve_substance(
        substance("Cobalt lithium manganese nickel oxide", "182442-95-1"))
    assert found["csid"] == "13289"


async def test_a_recorded_choice_is_used(tmp_path):
    mapping = SubstanceMapping(tmp_path / "m.json")
    mapping.entries["cas:9009-54-5"] = {"csid": "8066"}
    made = backend(POLYURETHANE, mapping)
    assert (await made.resolve_substance(substance("Polyurethane", "9009-54-5")))["csid"] == "8066"


async def test_a_choice_camds_no_longer_offers_is_refused_not_ignored(tmp_path):
    """Silently falling back would import a different substance than the one
    that was approved."""
    mapping = SubstanceMapping(tmp_path / "m.json")
    mapping.entries["cas:9009-54-5"] = {"csid": "99999"}
    made = backend(POLYURETHANE, mapping)
    with pytest.raises(CamdsApiError, match="no longer one of"):
        await made.resolve_substance(substance("Polyurethane", "9009-54-5"))


def test_an_unanswered_question_keeps_the_rows_it_was_asked_about(tmp_path):
    path = tmp_path / "m.json"
    mapping = SubstanceMapping(path)
    mapping.ask(substance("Polyurethane", "9009-54-5"), POLYURETHANE)
    mapping.save()

    saved = json.loads(path.read_text(encoding="utf-8"))["entries"]["cas:9009-54-5"]
    assert saved["csid"] is None, "an unanswered entry is a question, not an answer"
    assert [r["enName"] for r in saved["candidates"]] == ["PUR", "polyurethane foam"]
    assert SubstanceMapping(path).unanswered == ["cas:9009-54-5"]
    assert SubstanceMapping(path).chosen(substance("Polyurethane", "9009-54-5")) is None


def test_asking_again_never_overwrites_an_answer(tmp_path):
    """A later check must not turn a decision back into a question."""
    path = tmp_path / "m.json"
    mapping = SubstanceMapping(path)
    mapping.entries["cas:9009-54-5"] = {"csid": "8066", "substance": "Polyurethane"}
    mapping.ask(substance("Polyurethane", "9009-54-5"), POLYURETHANE)
    assert mapping.entries["cas:9009-54-5"]["csid"] == "8066"
    assert mapping.unanswered == []


def test_a_substance_without_a_cas_is_keyed_on_its_name(tmp_path):
    from camds_imds_importer.camds.substance_mapping import key

    assert key(substance("Epoxy Resin")) == "name:epoxy resin"
    assert key(substance("Silicon", "7440-21-3")) == "cas:7440-21-3"
    assert key(substance("Misc., not to declare", "system")) == "name:misc., not to declare"


async def test_read_back_looks_for_what_was_attached_not_what_the_report_called_it(tmp_path):
    """The catalogue's name and the report's differ as soon as a row is taken
    whose name is not the report's. Verifying the report's name would look for
    something that was never written."""
    from camds_imds_importer.camds.api_backend import ApiBackend

    made = ApiBackend(object(), SubstanceMapping(tmp_path / "m.json"))
    made._paths = {("Adhesive",): ["CA_21_1"]}
    made._children = {"CA_21_1": ["CA_21_2"]}
    node = substance("PESTS")

    async def load(struts_id):
        made.view = {"data": {"ccasCode": None, "cenName": "Polyester Stryrene Copolymer"},
                     "structureVO": {"crateType": 2, "crate": 1, "cminRate": 0, "cmaxRate": 0}}
        return made.view

    made._load = load
    await made.verify_substance(["Adhesive", "Polyester Stryrene Copolymer"], node)

    with pytest.raises(CamdsApiError, match="is missing"):
        await made.verify_substance(["Adhesive", "PESTS"], node)
