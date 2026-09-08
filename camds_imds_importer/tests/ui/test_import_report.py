"""What the operator is told when an import ends.

A run of the real report takes hours, so nobody is watching when it finishes.
The outcome used to go to a status line and the log, where a run that had
published 52 Materials and one that died on the first node looked the same.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from camds_imds_importer.ui.import_report import duration, failure, summary  # noqa: E402


@pytest.mark.parametrize("seconds, said", [
    (0, "0s"), (42.4, "42s"), (60, "1m 00s"), (95, "1m 35s"),
    (3600, "1h 00m"), (21902, "6h 05m"),
])
def test_a_length_of_time_reads_the_way_somebody_would_say_it(seconds, said):
    assert duration(seconds) == said


def test_a_finished_import_reports_what_it_did_and_how_long_it_took():
    title, body = summary(
        {"kind": "import_tree", "nodes": 245, "total": 245, "identity": "CA_5_1/0.01",
         "note": "Draft tree saved and verified.",
         "skipped": ["Cu99: reused CA_8_9/6 already in CAMDS",
                     "PI: released as CA_8_10/1"],
         "warnings": ["GLASS: application left unset"]},
        parse_seconds=13, import_seconds=4210, nodes=245)

    assert title == "Import complete"
    assert "245 of 245 steps verified" in body
    assert "Parsed 245 nodes in 13s" in body
    assert "Imported in 1h 10m" in body
    assert "1 Material(s) already in CAMDS were reused" in body
    assert "1 Material(s) were released" in body
    assert "1 item(s) imported as declared" in body
    assert "CA_5_1/0.01" in body


def test_a_run_with_nothing_to_report_says_only_what_happened():
    _title, body = summary({"kind": "import_tree", "nodes": 9, "total": 9, "note": "done"},
                           parse_seconds=1, import_seconds=30, nodes=9)
    for absent in ("reused", "released", "imported as declared", "left unset"):
        assert absent not in body, absent


def test_a_stopped_import_says_how_far_it_got_and_what_stands():
    """Every completed Save stands, and the journal is what lets the run
    continue instead of starting again - which is the thing to say first."""
    title, body = failure("Cu99: expected CA_8_9/0.01, CAMDS has CA_8_9/1",
                          parse_seconds=13, import_seconds=2400,
                          done=52, total=245, journal="output/camds_imports/abc.jsonl")

    assert title == "Import stopped"
    assert "52 of 245 steps done" in body
    assert "40m 00s" in body
    assert "CAMDS has CA_8_9/1" in body
    assert "not rolled back" in body
    assert "output/camds_imports/abc.jsonl" in body


def test_a_failure_with_no_journal_does_not_invent_one():
    _title, body = failure("session expired", parse_seconds=1, import_seconds=2,
                           done=0, total=245, journal=None)
    assert "Resume" not in body


def test_both_outcomes_reach_the_operator_who_is_not_watching(monkeypatch):
    """Hours pass, and the window is behind something else by the end."""
    from PySide6.QtWidgets import QApplication, QMessageBox

    from camds_imds_importer.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    window = MainWindow()
    shown, alerted = [], []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    monkeypatch.setattr(QApplication, "alert", staticmethod(lambda w, *a: alerted.append(w)))

    window._import_finished({"kind": "import_tree", "nodes": 1, "total": 1, "note": "done"})
    window._import_failed("it broke")

    assert len(shown) == 2, "a finished run and a stopped one both say so"
    assert len(alerted) == 2, "the taskbar is flashed rather than the window raised"
    assert "it broke" in shown[1]
    assert "Build:" not in shown[0]


def test_what_was_left_out_is_named_in_the_summary():
    """A tree with something missing and no word about what is worse than one
    that says so: nobody can see it by looking at what was imported."""
    from camds_imds_importer.ui.import_report import summary

    result = {"nodes": 40, "total": 40, "identity": "CA_5_1/0.01", "note": "",
              "skipped": ["PBT: not imported, at Parent / Child / PBT",
                          "PBT: left out because 'ISO 1043-4 FR(17) …' could not be identified "
                          "in the CAMDS catalogue, IMDS having cut that name short",
                          "Steel: reused CA_8_9/1 already in CAMDS"]}
    title, body = summary(result, parse_seconds=15, import_seconds=90, nodes=4293)
    assert title == "Import complete"
    assert "1 node(s) were left out of the import, at:" in body
    assert "  PBT: not imported, at Parent / Child / PBT" in body
    assert "1 Material(s) already in CAMDS were reused." in body
