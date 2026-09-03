from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ImportStage(StrEnum):
    IDLE = "IDLE"
    PDF_LOADING = "PDF_LOADING"
    PDF_PARSING = "PDF_PARSING"
    TREE_BUILDING = "TREE_BUILDING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    CAMDS_STARTING = "CAMDS_STARTING"
    CAMDS_LOGIN = "CAMDS_LOGIN"
    CAMDS_WAITING_VERIFICATION = "CAMDS_WAITING_VERIFICATION"
    CAMDS_AUTHENTICATED = "CAMDS_AUTHENTICATED"
    CAMDS_MAPPING = "CAMDS_MAPPING"
    CAMDS_IMPORTING = "CAMDS_IMPORTING"
    CAMDS_VERIFYING = "CAMDS_VERIFYING"
    CAMDS_SAVING = "CAMDS_SAVING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class ImportProgress:
    stage: ImportStage = ImportStage.IDLE
    total_nodes: int = 0
    completed_nodes: int = 0
    total_components: int = 0
    completed_components: int = 0
    total_materials: int = 0
    completed_materials: int = 0
    total_substances: int = 0
    completed_substances: int = 0
    warnings: int = 0
    errors: int = 0
    current_node_uid: str | None = None
    current_node_name: str | None = None
    elapsed_seconds: float = 0.0


STAGE_PERCENT: dict[ImportStage, int] = {
    ImportStage.IDLE: 0, ImportStage.PDF_LOADING: 1, ImportStage.PDF_PARSING: 15,
    ImportStage.TREE_BUILDING: 25, ImportStage.VALIDATING: 30, ImportStage.READY: 30,
    ImportStage.CAMDS_STARTING: 32, ImportStage.CAMDS_LOGIN: 34,
    ImportStage.CAMDS_WAITING_VERIFICATION: 34, ImportStage.CAMDS_AUTHENTICATED: 35,
    ImportStage.CAMDS_MAPPING: 40, ImportStage.CAMDS_IMPORTING: 40,
    ImportStage.CAMDS_VERIFYING: 98, ImportStage.CAMDS_SAVING: 99,
    ImportStage.PAUSED: 0, ImportStage.COMPLETED: 100,
    ImportStage.FAILED: 0, ImportStage.CANCELLED: 0,
}
