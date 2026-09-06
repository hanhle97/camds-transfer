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


def test_wrapped_hyphen_is_rejoined_without_eating_characters():
    """A backreference typo here silently mangled every chemical name."""
    from camds_imds_importer.parser.row_parser import _clean
    assert _clean("Polyurethan and PE- lubricant") == "Polyurethan and PE-lubricant"
    assert _clean("alpha- hydro-omega-((1-oxo-2-propen-1- yl)oxy)-") == \
        "alpha-hydro-omega-((1-oxo-2-propen-1-yl)oxy)-"
    # A hyphen standing on its own is a separator, not a wrapped word.
    assert _clean("8(e) - Lead in high melting") == "8(e) - Lead in high melting"
    assert _clean("1,4-Benzenedicarboxylic acid") == "1,4-Benzenedicarboxylic acid"
    # Nothing may be lost: rejoining only removes the spaces after the hyphen.
    for text in ("lead- based", "1,4- Benzene", "PE- lubricant"):
        assert len(_clean(text)) == len(text) - 1
        assert all(char.isprintable() for char in _clean(text))
