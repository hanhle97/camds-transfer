from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf

from ..parser.models import MDSDocument, MDSNode


HEADERS = ["Level", "Type", "Name", "Part number", "Material number", "CAS", "IMDS ID", "Quantity", "Weight (g)", "Percentage", "Source page"]


def rows(document: MDSDocument) -> list[list[object]]:
    result: list[list[object]] = []
    def visit(node: MDSNode) -> None:
        result.append([node.level, node.node_type.value, node.name, node.part_number or "", node.material_number or "", node.cas_number or "", node.imds_id or "", node.quantity if node.quantity is not None else "", node.weight_g if node.weight_g is not None else "", node.percentage if node.percentage is not None else "", node.source_page])
        for child in node.children:
            visit(child)
    visit(document.root)
    return result


def export_excel(document: MDSDocument, path: Path) -> None:
    values = [HEADERS, *rows(document)]
    def cell(value: object, col: int, row: int) -> str:
        ref = ""
        n = col
        while n:
            n, rem = divmod(n - 1, 26); ref = chr(65 + rem) + ref
        ref += str(row)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
    sheet_rows = "".join(f'<row r="{r}">{"".join(cell(v, c, r) for c, v in enumerate(vals, 1))}</row>' for r, vals in enumerate(values, 1))
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="MDS Data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet_rows}</sheetData></worksheet>')


def export_pdf(document: MDSDocument, path: Path) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(); lines = ["CAMDS IMDS Parsed Data", json.dumps(asdict(document.metadata), ensure_ascii=False, default=str), ""]
    lines += [" | ".join(map(str, row)) for row in [HEADERS, *rows(document)]]
    for start in range(0, len(lines), 45):
        if start: page = pdf.new_page()
        page.insert_textbox((36, 36, 560, 800), "\n".join(lines[start:start + 45]), fontsize=8)
    pdf.save(path); pdf.close()
