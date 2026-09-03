from __future__ import annotations

import pymupdf
from PySide6.QtWidgets import QApplication, QFileDialog

from camds_imds_importer.core.state_machine import AppState
from camds_imds_importer.ui.main_window import MainWindow


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_progress_and_state_updates(monkeypatch) -> None:
    app()
    window = MainWindow()
    window._set_overall(48)
    window._set_stage("CAMDS_IMPORTING")
    assert window.overall.value() == 48
    assert "Camds Importing" in window.stage_label.text()
    window.state_machine.transition(AppState.DOCUMENT_LOADED)
    assert window.parse_button.isEnabled()


def test_import_pdf_selection_is_nonblocking_entry(monkeypatch, tmp_path) -> None:
    app()
    pdf_path = tmp_path / "sample.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf_path)
    document.close()
    window = MainWindow()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(pdf_path), "PDF files (*.pdf)"))
    monkeypatch.setattr(window, "start_parse", lambda: None)
    window.select_pdf()
    assert window.source_path == pdf_path
    assert window.state_machine.state == AppState.DOCUMENT_LOADED
    assert "1 pages" in window.file_label.text()
