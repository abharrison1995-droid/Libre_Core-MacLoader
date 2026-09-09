"""Metadata-only ACPI capture contracts; no imported AML is executed."""

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple
import re

from macloader.domain.contracts import canonical_json_digest
from macloader.domain.evidence import EvidenceCompleteness, EvidenceConfidence, EvidenceRecord


@dataclass(frozen=True)
class AcpiTableRecord:
    table_name: str
    sha256: str
    source: str
    namespace_paths: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256.lower()):
            raise ValueError("ACPI table digest must be a lowercase SHA-256 digest")
        if not self.table_name.strip() or not self.source.strip():
            raise ValueError("ACPI table name and source are required")

    def to_dict(self) -> Dict[str, Any]:
        return {"table_name": self.table_name, "sha256": self.sha256.lower(), "source": self.source, "namespace_paths": list(self.namespace_paths)}


@dataclass(frozen=True)
class AcpiEvidenceBundle:
    snapshot_id: str
    bios_binding: str
    private_ref: str
    capture_version: str
    tables: Tuple[AcpiTableRecord, ...] = field(default_factory=tuple)
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1", "snapshot_id": self.snapshot_id, "bios_binding": self.bios_binding,
            "private_ref": self.private_ref, "capture_version": self.capture_version,
            "tables": [table.to_dict() for table in self.tables], "confidence": self.confidence.value,
        }

    @property
    def complete(self) -> bool:
        return bool(self.tables) and len({table.table_name.casefold() for table in self.tables}) == len(self.tables)

    def to_evidence_record(self) -> EvidenceRecord:
        data = {
            "snapshot_id": self.snapshot_id, "bios_binding": self.bios_binding,
            "capture_version": self.capture_version, "tables": [table.to_dict() for table in self.tables],
        }
        status = EvidenceCompleteness.COMPLETE if self.complete else EvidenceCompleteness.PARTIAL if self.tables else EvidenceCompleteness.MISSING
        unresolved = () if status == EvidenceCompleteness.COMPLETE else ("ACPI table capture is incomplete",)
        return EvidenceRecord(
            evidence_id=f"acpi-bundle-{self.snapshot_id}", kind="acpi", schema_version="1",
            digest=canonical_json_digest(data), private_ref=self.private_ref,
            machine_snapshot_id=self.snapshot_id, bios_binding=self.bios_binding,
            capture_method="acpi-table-capture", capture_version=self.capture_version,
            completeness=status, confidence=self.confidence, unresolved_checks=unresolved,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "AcpiEvidenceBundle":
        if not isinstance(data, dict):
            raise ValueError("ACPI evidence bundle must be a mapping")
        required = {"schema_version", "snapshot_id", "bios_binding", "private_ref", "capture_version", "tables", "confidence"}
        if set(data) != required or data["schema_version"] != "1":
            raise ValueError("ACPI evidence bundle has an invalid schema")
        if not isinstance(data["tables"], list):
            raise ValueError("ACPI tables must be a list")
        tables = []
        for raw in data["tables"]:
            if not isinstance(raw, dict) or set(raw) != {"table_name", "sha256", "source", "namespace_paths"}:
                raise ValueError("ACPI table record has missing or unknown fields")
            paths = raw["namespace_paths"]
            if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
                raise ValueError("ACPI namespace_paths must be a list of strings")
            tables.append(AcpiTableRecord(str(raw["table_name"]), str(raw["sha256"]), str(raw["source"]), tuple(paths)))
        return cls(
            snapshot_id=str(data["snapshot_id"]), bios_binding=str(data["bios_binding"]),
            private_ref=str(data["private_ref"]), capture_version=str(data["capture_version"]),
            tables=tuple(tables), confidence=EvidenceConfidence(str(data["confidence"])),
        )
