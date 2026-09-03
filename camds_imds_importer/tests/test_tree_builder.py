import unittest

from camds_imds_importer.parser.models import MDSNode, NodeType
from camds_imds_importer.parser.tree_builder import build_tree


def node(level: int, name: str, **kwargs: object) -> MDSNode:
    return MDSNode(uid="", level=level, node_type=NodeType.UNKNOWN, name=name, source_page=1, source_text=name, **kwargs)


class TreeBuilderTests(unittest.TestCase):
    def test_builds_arbitrary_depth_and_infers_types(self) -> None:
        rows = [
        node(1, "Product", part_number="P1", weight_g=10.0),
        node(2, "Component", part_number="C1", weight_g=10.0),
        node(3, "Steel", classification="1.1.1: Steel", weight_g=10.0),
        node(4, "Iron", cas_number="7439-89-6", percentage=100.0),
    ]

        root, warnings = build_tree(rows)

        self.assertEqual(warnings, [])
        self.assertEqual(root.children[0].children[0].children[0].name, "Iron")
        self.assertEqual(root.node_type, NodeType.COMPONENT)
        self.assertEqual(root.children[0].children[0].node_type, NodeType.MATERIAL)
        self.assertEqual(root.children[0].children[0].children[0].node_type, NodeType.SUBSTANCE)


    def test_level_jump_is_preserved_and_warned(self) -> None:
        root, warnings = build_tree([node(1, "Root"), node(4, "Unexpected")])
        self.assertEqual(root.children[0].name, "Unexpected")
        self.assertTrue(any("level jump" in warning.lower() for warning in warnings))
