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


def backend(rows, mapping):
    return ApiBackend(CamdsApi(Catalogue(rows), base_url="https://camds.test"), mapping)


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


async def test_a_shared_cas_the_report_does_not_name_is_still_a_question(tmp_path):
    """"Polyurethane" is neither "PUR" nor "polyurethane foam"; picking either
    would be a statement about the material that nobody made."""
    made = backend(POLYURETHANE, SubstanceMapping(tmp_path / "m.json"))
    with pytest.raises(CamdsApiError, match="matched 2 entries"):
        await made.resolve_substance(substance("Polyurethane", "9009-54-5"))
    assert [n["name"] for n, _ in made.pending] == ["Polyurethane"]


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
    mapping.entries[""] = {}
    mapping.entries.clear()
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
