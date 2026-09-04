from camds_imds_importer.camds.dry_run import build_dry_run_plan


def test_dry_run_plan_is_recursive_and_non_mutating() -> None:
    root = {
        "uid": "root", "node_type": "COMPONENT", "name": "Assembly", "children": [
            {"uid": "s", "node_type": "SUBSTANCE", "name": "Copper", "cas_number": "7440-50-8", "children": []},
        ],
    }
    plan = build_dry_run_plan(root)
    assert [operation.action for operation in plan] == ["INSPECT_COMPONENT", "SEARCH_SUBSTANCE_BY_CAS"]
    assert plan[1].fields["cas_number"] == "7440-50-8"
    assert root["children"][0]["cas_number"] == "7440-50-8"
