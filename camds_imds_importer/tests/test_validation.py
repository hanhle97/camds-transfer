import unittest

from camds_imds_importer.parser.models import MDSNode, NodeType
from camds_imds_importer.validation.validator import validate_document


class ValidationTests(unittest.TestCase):
    def test_composition_range_containing_100_is_valid(self) -> None:
        material = MDSNode(uid="m", level=1, node_type=NodeType.MATERIAL, name="Alloy", source_page=1, source_text="Alloy")
        material.children = [
        MDSNode(uid="a", level=2, node_type=NodeType.SUBSTANCE, name="A", percentage_min=20, percentage_max=30, source_page=1, source_text="A"),
        MDSNode(uid="b", level=2, node_type=NodeType.SUBSTANCE, name="B", percentage_min=70, percentage_max=80, source_page=1, source_text="B"),
    ]
        issues = validate_document(material)
        self.assertFalse([issue for issue in issues if issue.severity == "ERROR"])
