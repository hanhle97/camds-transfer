from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    COMPONENT = "COMPONENT"
    SEMICOMPONENT = "SEMICOMPONENT"
    MATERIAL = "MATERIAL"
    SUBSTANCE = "SUBSTANCE"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class PartyData:
    name: str | None = None
    identifier: str | None = None
    part_number: str | None = None
    part_name: str | None = None
    supplier_code: str | None = None


@dataclass(slots=True)
class MDSMetadata:
    imds_id: str | None = None
    version: str | None = None
    part_number: str | None = None
    description: str | None = None
    weight_g: float | None = None
    node_id: str | None = None
    mds_status: str | None = None


@dataclass(slots=True)
class MDSNode:
    uid: str
    level: int
    node_type: NodeType
    name: str
    part_number: str | None = None
    material_number: str | None = None
    cas_number: str | None = None
    imds_id: str | None = None
    imds_version: str | None = None
    quantity: float | None = None
    weight_g: float | None = None
    percentage: float | None = None
    percentage_min: float | None = None
    percentage_max: float | None = None
    is_rest: bool = False
    classification: str | None = None
    gadsl: str | None = None
    svhc: bool | None = None
    application_id: str | None = None
    application_text: str | None = None
    source_page: int = 0
    source_text: str = ""
    children: list[MDSNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["node_type"] = self.node_type.value
        return data


@dataclass(slots=True)
class MDSDocument:
    metadata: MDSMetadata
    supplier: PartyData
    recipient: PartyData
    root: MDSNode
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": asdict(self.metadata),
            "supplier": asdict(self.supplier),
            "recipient": asdict(self.recipient),
            "root": self.root.to_dict(),
            "warnings": self.warnings,
        }

