from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from camds_imds_importer.core.statistics import calculate_statistics, first_tree_lines
from camds_imds_importer.parser.pdf_parser import parse_pdf
from camds_imds_importer.validation.validator import validate_document


def parse_command(input_pdf: Path, output_dir: Path | None) -> int:
    document = parse_pdf(input_pdf)
    issues = validate_document(document.root)
    document.warnings.extend(f"{issue.severity}: {issue.message} ({issue.node_uid})" for issue in issues)
    target = output_dir or Path("output") / input_pdf.stem
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "mds.json"
    warnings_path = target / "parse_warnings.json"
    stats_path = target / "tree_statistics.json"
    json_path.write_text(json.dumps(document.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    warnings_path.write_text(json.dumps(document.warnings, indent=2, ensure_ascii=False), encoding="utf-8")
    statistics = calculate_statistics(document)
    stats_path.write_text(json.dumps(statistics.to_dict(), indent=2), encoding="utf-8")
    print(json.dumps(statistics.to_dict(), indent=2))
    print("\nFirst 30 parsed nodes:")
    print("\n".join(first_tree_lines(document.root)))
    print(f"\nWrote {json_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an IMDS MDS Report PDF into canonical JSON")
    subparsers = parser.add_subparsers(dest="command")
    parse_parser = subparsers.add_parser("parse", help="Parse an IMDS report")
    parse_parser.add_argument("input_pdf", type=Path)
    parse_parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.command == "parse":
        return parse_command(args.input_pdf, args.output_dir)
    from PySide6.QtWidgets import QApplication
    from camds_imds_importer.ui import branding
    from camds_imds_importer.ui.main_window import MainWindow

    application = QApplication(sys.argv)
    application.setApplicationName("CAMDS IMDS Importer")
    branding.apply(application)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
