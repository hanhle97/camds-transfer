from __future__ import annotations

from .models import MDSNode, NodeType


def _infer_type(node: MDSNode) -> NodeType:
    if node.cas_number is not None or node.percentage is not None or node.percentage_min is not None or node.is_rest:
        return NodeType.SUBSTANCE
    if node.classification or (node.children and all(child.cas_number is not None for child in node.children)):
        return NodeType.MATERIAL
    if node.part_number or node.quantity is not None or node.children:
        return NodeType.COMPONENT
    return NodeType.UNKNOWN


def build_tree(rows: list[MDSNode]) -> tuple[MDSNode, list[str]]:
    if not rows:
        raise ValueError("No hierarchy rows were extracted")
    warnings: list[str] = []
    stack: list[MDSNode] = []
    roots: list[MDSNode] = []
    for node in rows:
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            parent = stack[-1]
            if node.level > parent.level + 1:
                warnings.append(
                    f"Page {node.source_page}: level jump from {parent.level} to {node.level} for {node.name!r}"
                )
            parent.children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    if len(roots) > 1:
        warnings.append(f"Found {len(roots)} root-level rows; attached additional roots beneath the first")
        roots[0].children.extend(roots[1:])
    root = roots[0]

    def infer(node: MDSNode) -> None:
        for child in node.children:
            infer(child)
        node.node_type = _infer_type(node)
        if node.node_type == NodeType.MATERIAL and node.material_number is None and node.part_number:
            node.material_number, node.part_number = node.part_number, None

    infer(root)
    return root, warnings
