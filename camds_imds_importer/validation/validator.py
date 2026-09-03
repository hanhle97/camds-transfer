from __future__ import annotations

from dataclasses import dataclass

from ..parser.models import MDSNode, NodeType


@dataclass(slots=True)
class ValidationIssue:
    severity: str
    node_uid: str
    message: str


def validate_document(root: MDSNode, composition_tolerance: float = 0.1) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def visit(node: MDSNode) -> None:
        if node.node_type == NodeType.MATERIAL and node.children:
            substances = [child for child in node.children if child.node_type == NodeType.SUBSTANCE]
            if substances and all(child.percentage is not None or child.percentage_min is not None for child in substances):
                minimum = sum(child.percentage if child.percentage is not None else child.percentage_min or 0 for child in substances)
                maximum = sum(child.percentage if child.percentage is not None else child.percentage_max or 0 for child in substances)
                if not minimum - composition_tolerance <= 100 <= maximum + composition_tolerance:
                    issues.append(ValidationIssue("WARNING", node.uid, f"Composition interval is {minimum:g}-{maximum:g}%"))
        for child in node.children:
            visit(child)

    visit(root)
    return issues
