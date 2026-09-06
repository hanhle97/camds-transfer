from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

import pdfplumber
from pypdf import PdfReader

from .models import MDSDocument, MDSMetadata, MDSNode, PartyData
from .row_parser import ColumnText, parse_row
from .tree_builder import build_tree

LABELS = (
    "Part/Item No.", "Description", "Weight", "IMDS ID / Version", "Node ID", "MDS Status",
    "Customer Part No.", "Customer Part Name", "Supplier Code", "Recipient:",
)


@dataclass(slots=True)
class RawRegion:
    page: int
    ordinal: int
    level: int
    columns: dict[str, list[str]] = field(default_factory=dict)

    def add(self, column: str, text: str) -> None:
        self.columns.setdefault(column, []).append(text)

    def value(self, column: str) -> str:
        return " ".join(self.columns.get(column, []))

    @property
    def source_text(self) -> str:
        return " | ".join(self.value(column) for column in COLUMN_ORDER if self.value(column))


COLUMN_ORDER = ("name", "identifier", "imds", "quantity", "weight", "portion", "portion_range", "class_flags", "application")
COLUMN_STARTS = (
    (680.0, "application"),
    (610.0, "class_flags"),
    (545.0, "portion_range"),
    (480.0, "portion"),
    (425.0, "weight"),
    (365.0, "quantity"),
    (280.0, "imds"),
    (195.0, "identifier"),
    (64.0, "name"),
)


def _preceding(text: str, label: str) -> str | None:
    index = text.find(label)
    if index < 0:
        return None
    line = text[:index].splitlines()[-1].strip(" -")
    return line or None


def _section_value(text: str, section: str, label: str) -> str | None:
    start = text.find(section)
    if start < 0:
        return None
    chunk = text[start:]
    index = chunk.find(label)
    if index < 0:
        return None
    lines = chunk[:index].splitlines()[1:]
    value_lines: list[str] = []
    for line in reversed(lines):
        cleaned = line.strip(" -")
        if not cleaned or any(marker in cleaned for marker in LABELS):
            if value_lines:
                break
            continue
        value_lines.append(cleaned)
        if len(value_lines) >= 3:
            break
    return " ".join(reversed(value_lines)) or None


def extract_metadata(text: str) -> tuple[MDSMetadata, PartyData, PartyData]:
    product_start = text.find("1.2 Product Identification")
    product_end = text.find("1.3 Recipient Data")
    product = text[product_start:product_end] if product_start >= 0 else text
    imds_matches = list(re.finditer(r"(\d+)\s*/\s*(\d+\.\d+)IMDS ID / Version", product))
    imds = imds_matches[-1] if imds_matches else None
    weight = _preceding(product, "Weight:")
    supplier_name = _section_value(text, "1.1 Supplier Data", "Name [ID]:")
    supplier_id_match = re.search(r"Name \[ID\]:\s*\n\[(\d+)\]", text)
    if supplier_name:
        supplier_name = re.sub(r"\s*\[\d+\]\s*$", "", supplier_name)
    recipient_name = _section_value(text, "1.3 Recipient Data", "Recipient:")
    recipient_id_match = re.search(r"\[(\d+)\]", recipient_name or "")
    if recipient_name:
        recipient_name = re.sub(r"\s*\[\d+\]\s*$", "", recipient_name)
    metadata = MDSMetadata(
        imds_id=imds.group(1) if imds else None,
        version=imds.group(2) if imds else None,
        part_number=_preceding(product, "Part/Item No.:"),
        description=_section_value(product, "1.2 Product Identification", "Description:"),
        weight_g=float(weight.lower().replace("g", "").replace(",", "").strip()) if weight else None,
        node_id=_preceding(product, "Node ID:"),
        mds_status=_preceding(product, "MDS Status:"),
    )
    supplier = PartyData(name=supplier_name, identifier=supplier_id_match.group(1) if supplier_id_match else None)
    recipient = PartyData(
        name=recipient_name,
        identifier=recipient_id_match.group(1) if recipient_id_match else None,
        part_number=_preceding(text[text.find("1.3 Recipient Data"):], "Customer Part No.:"),
        part_name=_section_value(text, "1.3 Recipient Data", "Customer Part Name:"),
        supplier_code=_preceding(text[text.find("1.3 Recipient Data"):], "Supplier Code:"),
    )
    return metadata, supplier, recipient


def _line_groups(words: list[dict[str, Any]], tolerance: float = 2.0) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        if not groups or abs(float(word["top"]) - float(groups[-1][0]["top"])) > tolerance:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def _marker(line: list[dict[str, Any]]) -> tuple[int, float] | None:
    left = [word for word in line if float(word["x0"]) < 64.0]
    for word in left:
        value = str(word["text"])
        if value.isdigit() and 1 <= int(value) <= 99:
            return int(value), float(word["top"])
    return None


def _column_for(x0: float) -> str | None:
    for start, name in COLUMN_STARTS:
        if x0 >= start:
            return name
    return None


def _page_regions(page: Any, page_number: int) -> list[RawRegion]:
    all_words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    header_candidates = [
        float(word["top"]) for word in all_words
        if str(word["text"]) == "Substance" and 60.0 <= float(word["x0"]) <= 150.0 and float(word["top"]) < 400.0
    ]
    body_start = max(header_candidates, default=162.0) + 18.0
    legend_tops = [float(word["top"]) for word in all_words if str(word["text"]) == "Legend" and float(word["x0"]) < 100.0]
    body_end = min(legend_tops, default=790.0)
    words = [word for word in all_words if body_start <= float(word["top"]) < body_end]
    lines = _line_groups(words)
    markers = [marker for line in lines if (marker := _marker(line))]
    regions: list[RawRegion] = []
    for marker_index, (level, marker_top) in enumerate(markers):
        previous_top = markers[marker_index - 1][1] if marker_index else body_start
        next_top = markers[marker_index + 1][1] if marker_index + 1 < len(markers) else body_end
        start_top = (previous_top + marker_top) / 2 if marker_index else body_start
        end_top = (marker_top + next_top) / 2 if marker_index + 1 < len(markers) else body_end
        region = RawRegion(page=page_number, ordinal=marker_index, level=level)
        for line in lines:
            line_top = float(line[0]["top"])
            if not start_top <= line_top < end_top:
                continue
            for word in sorted(line, key=lambda item: float(item["x0"])):
                x0 = float(word["x0"])
                if x0 < 64.0:
                    continue
                column = _column_for(x0)
                if column:
                    region.add(column, str(word["text"]))
        regions.append(region)
    return regions


def _region_to_node(region: RawRegion) -> MDSNode:
    class_flags = region.value("class_flags")
    # CAMDS publishes letter-suffixed classifications such as 5.1.a and 5.1.b;
    # without the suffix the code fell through into the GADSL flags column.
    class_match = re.match(r"(?P<class>\d+(?:\.\d+)+(?:\.[A-Za-z])?\s*:\s*.*?)(?=\s+(?:D|P|SVHC)(?:\s|$)|$)", class_flags)
    classification = class_match.group("class") if class_match else ""
    flags = class_flags[len(classification):].strip() if classification else class_flags
    columns = ColumnText(
        name=region.value("name"), identifier=region.value("identifier"), imds=region.value("imds"),
        quantity=region.value("quantity"), weight=region.value("weight"), portion=region.value("portion"),
        portion_range=region.value("portion_range"), classification=classification, flags=flags,
        application=region.value("application"),
    )
    return parse_row(
        level=region.level, columns=columns, source_page=region.page,
        source_text=region.source_text, source_ordinal=region.ordinal,
    )


def parse_pdf(path: Path, progress_callback: Callable[[int, int], None] | None = None) -> MDSDocument:
    if not path.is_file():
        raise FileNotFoundError(path)
    reader = PdfReader(path)
    first_page_text = reader.pages[0].extract_text() or ""
    metadata, supplier, recipient = extract_metadata(first_page_text)
    rows: list[MDSNode] = []
    warnings: list[str] = []
    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages[1:], start=2):
            try:
                rows.extend(_region_to_node(region) for region in _page_regions(page, page_number))
            except Exception as exc:
                warnings.append(f"Page {page_number}: extraction failed: {exc}")
            if progress_callback:
                progress_callback(page_number, total_pages)
    root, tree_warnings = build_tree(rows)
    warnings.extend(tree_warnings)
    return MDSDocument(metadata=metadata, supplier=supplier, recipient=recipient, root=root, warnings=warnings)
