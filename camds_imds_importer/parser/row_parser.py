from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .models import MDSNode, NodeType

CAS_RE = re.compile(r"^\d{2,6}-\d{2}-\d$")
IMDS_RE = re.compile(r"(?P<id>\d+)\s*/\s*(?P<version>\d+\.\d+)")
RANGE_RE = re.compile(r"(?P<minimum>\d+(?:\.\d+)?)\s*-\s*(?P<maximum>\d+(?:\.\d+)?)")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
APPLICATION_RE = re.compile(r"\[(?P<id>\d+)\]\s*$")
CLASSIFICATION_RE = re.compile(r"^\d+(?:\.\d+)+(?:\s*:\s*.+)?$")


@dataclass(slots=True)
class ColumnText:
    name: str = ""
    identifier: str = ""
    imds: str = ""
    quantity: str = ""
    weight: str = ""
    portion: str = ""
    portion_range: str = ""
    classification: str = ""
    flags: str = ""
    application: str = ""


def _number(value: str) -> float | None:
    match = NUMBER_RE.search(value.replace(",", ""))
    return float(match.group()) if match else None


def _clean(value: str) -> str | None:
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def parse_row(*, level: int, columns: ColumnText, source_page: int, source_text: str, source_ordinal: int = 0) -> MDSNode:
    name = _clean(columns.name) or f"Unnamed node on page {source_page}"
    identifier = _clean(columns.identifier)
    imds_match = IMDS_RE.search(columns.imds)
    percentage_text = " ".join(part for part in (columns.portion, columns.portion_range) if part)
    range_match = RANGE_RE.search(percentage_text)
    is_rest = "rest" in percentage_text.lower()
    single_percentage = None if range_match else _number(percentage_text)
    cas_number = identifier if identifier and CAS_RE.fullmatch(identifier) else None
    if identifier == "system":
        cas_number = "system"

    classification = _clean(columns.classification)
    combined_flags = _clean(columns.flags)
    if classification and not CLASSIFICATION_RE.match(classification):
        combined_flags = _clean(" ".join(filter(None, (classification, combined_flags))))
        classification = None

    application = _clean(columns.application)
    application_match = APPLICATION_RE.search(application or "")
    digest = hashlib.sha1(f"{source_page}|{source_ordinal}|{level}|{source_text}".encode("utf-8")).hexdigest()[:16]
    quantity = _number(columns.quantity)
    material_number = identifier if identifier and not cas_number and classification else None
    part_number = identifier if identifier and not cas_number and not material_number else None

    return MDSNode(
        uid=f"n-{digest}",
        level=level,
        node_type=NodeType.UNKNOWN,
        name=name,
        part_number=part_number,
        material_number=material_number,
        cas_number=cas_number,
        imds_id=imds_match.group("id") if imds_match else None,
        imds_version=imds_match.group("version") if imds_match else None,
        quantity=quantity,
        weight_g=_number(columns.weight),
        percentage=single_percentage,
        percentage_min=float(range_match.group("minimum")) if range_match else None,
        percentage_max=float(range_match.group("maximum")) if range_match else None,
        is_rest=is_rest,
        classification=classification,
        gadsl=combined_flags,
        svhc="SVHC" in (combined_flags or "").upper() or None,
        application_id=application_match.group("id") if application_match else None,
        application_text=application,
        source_page=source_page,
        source_text=" ".join(source_text.split()),
    )
