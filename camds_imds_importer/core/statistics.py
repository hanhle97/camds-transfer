from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from ..parser.models import MDSDocument, MDSNode, NodeType


@dataclass(slots=True)
class TreeStatistics:
    total_nodes: int
    maximum_depth: int
    components: int
    materials: int
    substances: int
    unknown_nodes: int
    rows_with_cas: int
    rows_with_ranges: int
    rows_with_rest: int
    warnings: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def walk(root: MDSNode):
    yield root
    for child in root.children:
        yield from walk(child)


def calculate_statistics(document: MDSDocument) -> TreeStatistics:
    nodes = list(walk(document.root))
    counts = Counter(node.node_type for node in nodes)
    return TreeStatistics(
        total_nodes=len(nodes), maximum_depth=max(node.level for node in nodes),
        components=counts[NodeType.COMPONENT], materials=counts[NodeType.MATERIAL],
        substances=counts[NodeType.SUBSTANCE], unknown_nodes=counts[NodeType.UNKNOWN] + counts[NodeType.SEMICOMPONENT],
        rows_with_cas=sum(bool(node.cas_number and node.cas_number != "system") for node in nodes),
        rows_with_ranges=sum(node.percentage_min is not None for node in nodes),
        rows_with_rest=sum(node.is_rest for node in nodes), warnings=len(document.warnings),
    )


def first_tree_lines(root: MDSNode, limit: int = 30) -> list[str]:
    lines: list[str] = []
    for node in walk(root):
        if len(lines) >= limit:
            break
        lines.append(f"{'  ' * (node.level - root.level)}- L{node.level} [{node.node_type.value}] {node.name}")
    return lines
