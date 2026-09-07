import unittest

from camds_imds_importer.parser.pdf_parser import extract_metadata


class PdfParserTests(unittest.TestCase):
    def test_extract_metadata_from_page_one_text(self) -> None:
        text = """1.1 Supplier Data
Robert Bosch GmbHName [ID]:
1.2 Product Identification
044200509KPart/Item No.:
U557E MY26 PHEV BATTERY ASM-. 12V PowernetDescription:
3,000.0 gWeight:
NoPreliminary MDS:
1388408019 / 11.000IMDS ID / Version:
1508976862Node ID:
Internally ReleasedMDS Status:
1.3 Recipient Data
SAIC General Motors Propulsion Systems [69064]Recipient:
26985704Customer Part No.:
"""
        metadata, supplier, recipient = extract_metadata(text)
        self.assertEqual(metadata.imds_id, "1388408019")
        self.assertEqual(metadata.version, "11.000")
        self.assertEqual(metadata.part_number, "044200509K")
        self.assertEqual(metadata.weight_g, 3000.0)
        self.assertEqual(supplier.name, "Robert Bosch GmbH")
        self.assertTrue((recipient.name or "").startswith("SAIC General Motors"))


def test_a_level_reads_the_same_whether_the_branch_touches_its_number():
    """Up to level 9 the left column prints "|- 8" and pdfplumber returns two
    words. From level 10 the extra digit takes the space, "|-10" arrives as one
    word, and reading only bare digits dropped every row below level 9 - eleven
    of them on one page of a real report. Their text was absorbed into the last
    row that had been recognised, which turned a Component into a Material whose
    substance names had been run together.
    """
    from camds_imds_importer.parser.pdf_parser import _marker

    def line(*texts):
        return [{"text": t, "x0": 40.0 + 6 * i, "top": 300.0} for i, t in enumerate(texts)]

    assert _marker(line("|-", "8")) == (8, 300.0)
    assert _marker(line("|-10")) == (10, 300.0)
    assert _marker(line("|-11")) == (11, 300.0)
    assert _marker(line("|-", "1")) == (1, 300.0)


def test_a_level_marker_is_only_read_from_the_left_column():
    """A number anywhere else on the row is a quantity, a weight or a CAS."""
    from camds_imds_importer.parser.pdf_parser import _marker

    assert _marker([{"text": "|-10", "x0": 300.0, "top": 300.0}]) is None
    assert _marker([{"text": "Tree", "x0": 40.0, "top": 300.0}]) is None
    assert _marker([{"text": "|-100", "x0": 40.0, "top": 300.0}]) is None
