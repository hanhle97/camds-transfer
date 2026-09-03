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
