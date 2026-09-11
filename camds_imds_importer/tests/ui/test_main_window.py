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


def test_page_progress_reports_remaining_pages_and_eta() -> None:
    app()
    window = MainWindow()
    window._parse_started_at = __import__("time").monotonic() - 10
    window._page_progress(100, 675)
    assert "page 100 / 675" in window.node_label.text()
    assert "575 remaining" in window.node_label.text()
    assert "pages/s" in window.progress_tab.labels["Rate"].text()
    assert "remaining" in window.progress_tab.labels["ETA"].text()


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


def test_the_help_menu_offers_a_mail_to_the_developer():
    """An operator stuck on something has the Logs tab and nowhere to send it."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from camds_imds_importer.ui import support
    from camds_imds_importer.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    window = MainWindow()
    actions = {}
    for menu in window.menuBar().findChildren(type(window.menuBar().addMenu("x"))):
        for action in menu.actions():
            actions.setdefault(action.text(), menu.title())

    assert actions.get("Report a problem to the developer") == "Help"
    assert support.DEVELOPER == "hanh.levan@vn.bosch.com"
    window.close()


def test_the_mail_carries_the_build_the_report_and_the_end_of_the_log():
    """The three things always asked for, so nobody has to ask."""
    from urllib.parse import parse_qs, unquote, urlparse

    from camds_imds_importer.ui import support

    url = support.report(build="4859a9c  built 2026-09-11 06:10",
                         report_name="MDSReport_E_F01ZD30020_1.pdf",
                         stage="CAMDS: Importing parsed tree…",
                         log="line one\nline two\nthe last thing that happened")

    assert url.startswith("mailto:hanh.levan@vn.bosch.com?")
    fields = parse_qs(urlparse(url).query)
    body = unquote(fields["body"][0])
    assert "4859a9c" in fields["subject"][0], "which build, in the subject"
    assert "MDSReport_E_F01ZD30020_1.pdf" in body
    assert "CAMDS: Importing parsed tree…" in body
    assert "the last thing that happened" in body
    assert "What I was doing:" in body, "room for the part only a person knows"


def test_a_long_log_keeps_its_end_and_says_what_was_dropped():
    """A mailto is cut short by the shell, silently and mid-word. What matters
    is the end of the log, so the beginning is what goes."""
    from urllib.parse import parse_qs, unquote, urlparse

    from camds_imds_importer.ui import support

    url = support.report(build="b", report_name="r", stage="s",
                         log=chr(10).join(f"line {i}" for i in range(500)))
    body = unquote(parse_qs(urlparse(url).query)["body"][0])

    assert body.rstrip().endswith("line 499"), "the end survives"
    assert "line 0" + chr(10) not in body, "the beginning is what goes"
    assert "earlier lines left out" in body, "and it says so"
    assert len(url) < 4000, "short enough for the shell to pass on"
