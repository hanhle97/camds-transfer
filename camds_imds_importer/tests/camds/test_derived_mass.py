"""A Component mass IMDS printed nowhere.

The Geely report leaves the weight column empty on an assembled row - a solder
paste applied to a joint - while every row around it carries one. A Component's
mass is what its contents weigh, so it is worked out rather than demanded from
the operator, and said out loud: it is the one number in the tree the report
did not state.
"""
import pytest

from camds_imds_importer.camds.import_plan import ImportRequest, children_mass


def substance(uid="s"):
    return {"uid": uid, "node_type": "SUBSTANCE", "name": "Tin", "cas_number": "7440-31-5",
            "percentage": 100, "children": []}


def material(uid="m", mass=0.006, name="F 640"):
    return {"uid": uid, "node_type": "MATERIAL", "name": name, "classification": "4.2",
            "weight_g": mass, "children": [substance("s" + uid)]}


def component(uid, name, mass=None, quantity=1, children=()):
    return {"uid": uid, "node_type": "COMPONENT", "name": name, "weight_g": mass,
            "quantity": quantity, "children": list(children)}


def test_a_component_with_no_mass_takes_what_its_contents_weigh():
    root = component("r", "Assembly", mass=0.006, children=[
        component("p", "Lötpaste", mass=None, children=[material()])])
    request = ImportRequest(root).snapshot()

    assert request.root["children"][0]["weight_g"] == pytest.approx(0.006)
    assert request.derived and "Lötpaste" in request.derived[0]
    assert "0.006 g" in request.derived[0], request.derived
    request.validate()


def test_a_declared_mass_is_never_replaced():
    """Even one that disagrees with its contents: that is the supplier's
    statement, and the deviation is reported instead."""
    root = component("r", "Assembly", mass=10.0, children=[
        component("p", "Part", mass=1.0, children=[material()])])
    request = ImportRequest(root).snapshot()

    assert request.root["weight_g"] == 10.0
    assert request.derived == []
    assert any("children total" in w for w in request.validate())


def test_a_child_with_no_mass_leaves_the_parent_blank():
    """Understating a mass would be worse than refusing to state one."""
    root = component("r", "Assembly", mass=None, children=[
        material("a", mass=0.5), material("b", mass=None)])
    request = ImportRequest(root).snapshot()

    assert request.root["weight_g"] is None
    assert request.derived == []
    with pytest.raises(ValueError, match="mass"):
        request.validate()


def test_the_mass_is_worked_out_from_the_bottom_up():
    """A Component whose own child was just filled in can be filled in too."""
    root = component("r", "Top", mass=None, children=[
        component("mid", "Middle", mass=None, quantity=3, children=[material(mass=2.0)])])
    request = ImportRequest(root).snapshot()

    assert request.root["children"][0]["weight_g"] == pytest.approx(2.0)
    assert request.root["weight_g"] == pytest.approx(6.0), "three of them"
    assert len(request.derived) == 2


def test_the_totalling_rule_is_the_one_the_deviation_check_uses():
    """A child Component counts once per item; anything else counts once."""
    node = component("r", "x", children=[
        component("c", "child", mass=2.0, quantity=3),
        material("m", mass=0.5),
        {"uid": "s", "node_type": "SEMICOMPONENT", "name": "semi", "weight_g": 1.0, "children": []},
    ])
    assert children_mass(node) == pytest.approx(2.0 * 3 + 0.5 + 1.0)

    node["children"][0]["quantity"] = None
    assert children_mass(node) is None, "unknowable, not smaller"


def test_a_derived_mass_reaches_the_operator_and_the_journal():
    """It is the one value in the tree that is ours rather than the supplier's."""
    import inspect

    from camds_imds_importer.camds import tree_import
    from camds_imds_importer.ui import import_dialog

    assert "request.derived" in inspect.getsource(import_dialog.ImportDialog)
    run = inspect.getsource(tree_import.TreeImporter.run)
    assert "derived=request.derived" in run, "journalled with the allocated ids"
    assert "request.derived" in run.split("return {")[-1], "reported with the result"
