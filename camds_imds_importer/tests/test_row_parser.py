import unittest

from camds_imds_importer.parser.row_parser import ColumnText, parse_row


class RowParserTests(unittest.TestCase):
    def test_parses_substance_range_and_rest(self) -> None:
        row = parse_row(
        level=5,
        columns=ColumnText(
            name="Nickel",
            identifier="7440-02-0",
            portion_range="Rest 99.5 - 100.0",
            flags="D",
            application="Other application [33]",
        ),
        source_page=6,
        source_text="|- 5 Nickel 7440-02-0 Rest 99.5 - 100.0 D Other application [33]",
    )

        self.assertEqual(row.cas_number, "7440-02-0")
        self.assertEqual(row.percentage_min, 99.5)
        self.assertEqual(row.percentage_max, 100.0)
        self.assertTrue(row.is_rest)
        self.assertEqual(row.application_id, "33")


    def test_distinguishes_weight_from_portion(self) -> None:
        row = parse_row(
        level=3,
        columns=ColumnText(name="EPDM", imds="1386026778 / 1.000", weight="17.5", classification="5.3: Elastomers"),
        source_page=2,
        source_text="|- 3 EPDM 1386026778 / 1.000 17.5 5.3: Elastomers",
    )

        self.assertEqual(row.weight_g, 17.5)
        self.assertIsNone(row.percentage)
        self.assertEqual(row.classification, "5.3: Elastomers")
