from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PlannedOperation:
    ordinal: int
    node_uid: str
    node_type: str
    name: str
    action: str
    fields: dict[str, Any]


def build_dry_run_plan(root: dict[str, Any]) -> list[PlannedOperation]:
    operations: list[PlannedOperation] = []

    def visit(node: dict[str, Any]) -> None:
        node_type = node.get("node_type", "UNKNOWN")
        action = "SEARCH_SUBSTANCE_BY_CAS" if node_type == "SUBSTANCE" and node.get("cas_number") not in (None, "system") else f"INSPECT_{node_type}"
        fields = {key: node.get(key) for key in ("part_number", "material_number", "cas_number", "quantity", "weight_g", "percentage", "percentage_min", "percentage_max", "classification") if node.get(key) is not None}
        operations.append(PlannedOperation(len(operations) + 1, node["uid"], node_type, node.get("name", ""), action, fields))
        for child in node.get("children", []):
            visit(child)

    visit(root)
    return operations


def write_dry_run_plan(document_dict: dict[str, Any], output_path: Path) -> list[PlannedOperation]:
    plan = build_dry_run_plan(document_dict["root"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps([asdict(operation) for operation in plan], indent=2, ensure_ascii=False), encoding="utf-8")
    return plan
