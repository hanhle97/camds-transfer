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


def working_directory() -> Path:
    """Where `config/`, `.runtime/` and `output/` are kept.

    Those paths are relative, which is right when the app is run from a checkout.
    A built executable can be started from anywhere - a Start-menu shortcut runs
    it from `C:\\Windows\\system32` - and the reviewed mappings, the saved session
    and the import journals would then be written wherever the shortcut happened
    to point. Beside the executable is a place the operator can find again.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


def check_command() -> int:
    """Say where this build looks for things and what it found.

    A built executable gives an operator nothing to inspect, and the first one
    failed with a path inside its own extraction directory that meant nothing
    to anybody. This prints what was actually resolved.
    """
    import os

    from camds_imds_importer.camds.browser_runtime import browsers_root, chromium_present

    frozen = getattr(sys, "frozen", False)
    print(f"Build            : {'executable' if frozen else 'source checkout'}")
    print(f"Python           : {sys.version.split()[0]}")
    print(f"Files kept in    : {working_directory()}")
    print(f"BROWSERS_PATH    : {os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '<unset>')}")
    print(f"Browser looked up: {browsers_root()}")
    found = chromium_present()
    print(f"Chromium         : {'found' if found else 'not installed yet'}")

    # What Python resolved and what the driver resolves are two different
    # answers, and only the second one decides whether a window opens.
    from playwright._impl._driver import get_driver_env
    print(f"Driver env       : {get_driver_env().get('PLAYWRIGHT_BROWSERS_PATH', '<unset>')}")

    import asyncio

    from playwright.async_api import async_playwright

    async def probe() -> str:
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True)
            version = browser.version
            await browser.close()
            return version

    try:
        print(f"Launch           : ok, Chromium {asyncio.run(probe())}")
    except Exception as exc:
        print(f"Launch           : FAILED - {str(exc).splitlines()[0][:200]}")
        return 1
    return 0


def main() -> int:
    import os

    os.chdir(working_directory())
    parser = argparse.ArgumentParser(description="Convert an IMDS MDS Report PDF into canonical JSON")
    subparsers = parser.add_subparsers(dest="command")
    parse_parser = subparsers.add_parser("parse", help="Parse an IMDS report")
    parse_parser.add_argument("input_pdf", type=Path)
    parse_parser.add_argument("--output-dir", type=Path)
    subparsers.add_parser("check", help="Report what this build resolved, and whether Chromium is present")
    args = parser.parse_args()
    if args.command == "check":
        return check_command()
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
