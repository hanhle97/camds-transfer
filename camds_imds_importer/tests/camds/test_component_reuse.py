"""Reusing a Component CAMDS already holds.

Three things have to agree, by instruction: the Part No., the number of
children, and every child pointing at the MDS this run resolved for it.
Anything else and a new Component is created.
"""
import pytest

from camds_imds_importer.camds.api import CamdsApi
from camds_imds_importer.camds.api_backend import ApiBackend


class Camds:
    def __init__(self, rows, trees):
        self.rows, self.trees, self.asked = rows, trees, []

    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        name = url.rsplit("/", 1)[-1]
        self.asked.append((name, params or {}, data or {}))
        if name == "findComponentByCondition":
            payload = {"records": self.rows}
        elif name == "loadMdsTree":
            payload = self.trees.get(params["mdsId"])
        else:
            payload = None

        class Response:
            status = 200

            @staticmethod
            async def json():
                return {"respCode": "0", "ok": True, "data": payload}
        return Response


def backend(camds):
    return ApiBackend(CamdsApi(camds, base_url="https://camds.test"))


def component(number="P1", children=("a", "b")):
    return {"uid": "c", "node_type": "COMPONENT", "name": "Bracket", "part_number": number,
            "weight_g": 1.0, "quantity": 1,
            "children": [{"uid": uid, "node_type": "MATERIAL", "name": uid} for uid in children]}


def tree(*child_mds):
    return {"id": "CA_21_1", "mdsId": "CA_5_9", "mdsCver": 2.0, "text": "Bracket",
            "children": [{"id": f"CA_21_{i}", "mdsId": m} for i, m in enumerate(child_mds)]}


RESOLVED = {"a": "CA_8_1", "b": "CA_8_2"}


async def test_a_component_with_the_same_number_and_the_same_children_is_reused():
    camds = Camds(rows=[{"mdsId": "CA_5_9", "symbol": "P1", "version": "2"}],
                  trees={"CA_5_9": tree("CA_8_1", "CA_8_2")})
    assert await backend(camds).find_existing_component(component(), RESOLVED) == ("CA_5_9", "2")


async def test_a_different_child_means_a_different_component():
    camds = Camds(rows=[{"mdsId": "CA_5_9", "symbol": "P1", "version": "2"}],
                  trees={"CA_5_9": tree("CA_8_1", "CA_8_99")})
    assert await backend(camds).find_existing_component(component(), RESOLVED) is None


async def test_a_different_number_of_children_means_a_different_component():
    camds = Camds(rows=[{"mdsId": "CA_5_9", "symbol": "P1", "version": "2"}],
                  trees={"CA_5_9": tree("CA_8_1")})
    assert await backend(camds).find_existing_component(component(), RESOLVED) is None


async def test_the_children_are_compared_in_order():
    """Two of the same references in a different order is a different assembly."""
    camds = Camds(rows=[{"mdsId": "CA_5_9", "symbol": "P1", "version": "2"}],
                  trees={"CA_5_9": tree("CA_8_2", "CA_8_1")})
    assert await backend(camds).find_existing_component(component(), RESOLVED) is None


async def test_a_child_this_run_has_not_resolved_stops_the_comparison():
    """Nothing to compare it against, so nothing can be claimed to match."""
    camds = Camds(rows=[], trees={})
    assert await backend(camds).find_existing_component(component(), {"a": "CA_8_1"}) is None
    assert camds.asked == [], "no point searching for something unmatchable"


async def test_a_component_with_no_part_number_is_not_searched_for():
    camds = Camds(rows=[], trees={})
    assert await backend(camds).find_existing_component(component(number=None), RESOLVED) is None
    assert camds.asked == []


async def test_only_released_versions_count_and_the_newest_wins():
    camds = Camds(
        rows=[{"mdsId": "CA_5_draft", "symbol": "P1", "version": "0.01"},
              {"mdsId": "CA_5_old", "symbol": "P1", "version": "1"},
              {"mdsId": "CA_5_new", "symbol": "P1", "version": "3"}],
        trees={"CA_5_old": tree("CA_8_1", "CA_8_2"), "CA_5_new": tree("CA_8_1", "CA_8_2"),
               "CA_5_draft": tree("CA_8_1", "CA_8_2")})
    assert await backend(camds).find_existing_component(component(), RESOLVED) == ("CA_5_new", "3")
    assert "CA_5_draft" not in [p.get("mdsId") for _n, p, _d in camds.asked]


async def test_a_row_whose_number_only_contains_the_search_is_not_a_match():
    camds = Camds(rows=[{"mdsId": "CA_5_9", "symbol": "P100", "version": "2"}],
                  trees={"CA_5_9": tree("CA_8_1", "CA_8_2")})
    assert await backend(camds).find_existing_component(component(), RESOLVED) is None


def test_a_matched_subtree_is_attached_and_not_walked_into():
    """Its children belong to its own MDS: building them again, or checking
    them field by field, would be describing somebody else's tree."""
    import inspect

    from camds_imds_importer.camds.tree_import import TreeImporter

    body = inspect.getsource(TreeImporter.run)
    assert 'if node["uid"] in matched:\n                        return' in body, \
        "paths must stop at an attached subtree"
    assert "add_component_reference" in body
    assert "expected the Component " in body, "read-back checks identity, and says so"


# --------------------------------------------------------- by number alone

async def test_the_number_alone_can_identify_a_component_when_asked_for():
    """The looser rule, and a mode of its own: what CAMDS holds under the
    number is taken as the part, whatever the report says is inside it."""
    camds = Camds(rows=[{"mdsId": "CA_5_9", "symbol": "P1", "version": "2"}], trees={})
    assert await backend(camds).find_component_by_number(component()) == ("CA_5_9", "2")
    assert not any(name == "loadMdsTree" for name, _p, _d in camds.asked),         "the contents are not looked at, which is the whole difference"


async def test_a_draft_is_not_a_component_anyone_can_attach():
    """0.01 is something somebody left half-built."""
    camds = Camds(rows=[{"mdsId": "CA_5_draft", "symbol": "P1", "version": "0.01"}], trees={})
    assert await backend(camds).find_component_by_number(component()) is None


async def test_the_highest_released_version_is_the_one_attached():
    camds = Camds(rows=[{"mdsId": "CA_5_old", "symbol": "P1", "version": "1"},
                        {"mdsId": "CA_5_new", "symbol": "P1", "version": "3"}], trees={})
    assert await backend(camds).find_component_by_number(component()) == ("CA_5_new", "3")


async def test_several_components_under_one_number_are_reported_not_chosen_quietly():
    camds = Camds(rows=[{"mdsId": "CA_5_a", "symbol": "P1", "version": "1"},
                        {"mdsId": "CA_5_b", "symbol": "P1", "version": "2"}], trees={})
    made = backend(camds)
    assert await made.find_component_by_number(component()) == ("CA_5_b", "2")
    assert any("2 Components in CAMDS carry the number P1" in note for note in made.findings)


async def test_a_row_whose_number_merely_contains_the_search_is_not_the_part():
    camds = Camds(rows=[{"mdsId": "CA_5_9", "symbol": "P100", "version": "2"}], trees={})
    assert await backend(camds).find_component_by_number(component()) is None


async def test_a_component_with_no_number_is_not_looked_up_by_one():
    camds = Camds(rows=[], trees={})
    assert await backend(camds).find_component_by_number(component(number=None)) is None
    assert camds.asked == []
