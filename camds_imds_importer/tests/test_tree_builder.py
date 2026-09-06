import unittest

from camds_imds_importer.parser.models import MDSNode, NodeType
from camds_imds_importer.parser.tree_builder import build_tree


def node(level: int, name: str, **kwargs: object) -> MDSNode:
    return MDSNode(uid="", level=level, node_type=NodeType.UNKNOWN, name=name, source_page=1, source_text=name, **kwargs)


class TreeBuilderTests(unittest.TestCase):
    def test_builds_arbitrary_depth_and_infers_types(self) -> None:
        rows = [
        # A Component declares a quantity; without one a node made of Materials
        # is a Semicomponent, which is what test_semicomponent_is_recognised covers.
        node(1, "Product", part_number="P1", weight_g=10.0, quantity=1.0),
        node(2, "Component", part_number="C1", weight_g=10.0, quantity=1.0),
        node(3, "Steel", classification="1.1.1: Steel", weight_g=10.0),
        node(4, "Iron", cas_number="7439-89-6", percentage=100.0),
    ]

        root, warnings = build_tree(rows)

        self.assertEqual(warnings, [])
        self.assertEqual(root.children[0].children[0].children[0].name, "Iron")
        self.assertEqual(root.node_type, NodeType.COMPONENT)
        self.assertEqual(root.children[0].children[0].node_type, NodeType.MATERIAL)
        self.assertEqual(root.children[0].children[0].children[0].node_type, NodeType.SUBSTANCE)


    def test_semicomponent_is_recognised_by_what_it_contains(self) -> None:
        # A Material is made of Substances. A node made of Materials that
        # declares no quantity of its own is a Semicomponent, not a Material
        # missing its classification.
        root, _ = build_tree([
            node(1, "Assembly", part_number="A1", weight_g=5.0, quantity=1.0),
            node(2, "White PP film", part_number="PP539-LN", weight_g=1.0),
            node(3, "PP", classification="5.1.b: unfilled Thermoplastics", percentage=90.0),
            node(4, "Propene homopolymer", cas_number="9003-07-0", percentage=100.0),
        ])
        film = root.children[0]
        self.assertEqual(root.node_type, NodeType.COMPONENT)
        self.assertEqual(film.node_type, NodeType.SEMICOMPONENT)
        self.assertEqual(film.children[0].node_type, NodeType.MATERIAL)

    def test_letter_suffixed_classification_is_kept(self) -> None:
        # CAMDS publishes 5.1.a and 5.1.b; they must not fall through to flags.
        root, _ = build_tree([
            node(1, "PA6-GF35 FR", classification="5.1.a: filled Thermoplastics", weight_g=1.0),
            node(2, "PA6", cas_number="25038-54-4", is_rest=True, percentage=60.0),
        ])
        self.assertEqual(root.node_type, NodeType.MATERIAL)
        self.assertEqual(root.classification, "5.1.a: filled Thermoplastics")

    def test_level_jump_is_preserved_and_warned(self) -> None:
        root, warnings = build_tree([node(1, "Root"), node(4, "Unexpected")])
        self.assertEqual(root.children[0].name, "Unexpected")
        self.assertTrue(any("level jump" in warning.lower() for warning in warnings))
