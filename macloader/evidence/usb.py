"""Sanitized USB port evidence with derived completeness."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from macloader.domain.contracts import canonical_json_digest
from macloader.domain.evidence import EvidenceCompleteness, EvidenceConfidence, EvidenceRecord


class UsbObservationState(str, Enum):
    OBSERVED = "observed"
    UNTESTED = "untested"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class UsbPortObservation:
    physical_label: str
    logical_port: str
    connector_type: str
    tested_speed: str
    controller_id: str
    orientation: Optional[str] = None
    internal_device: bool = False
    state: UsbObservationState = UsbObservationState.OBSERVED

    def __post_init__(self) -> None:
        if not all(isinstance(item, str) and item.strip() for item in (self.physical_label, self.logical_port, self.connector_type, self.tested_speed, self.controller_id)):
            raise ValueError("USB physical label, logical port, connector, speed and controller are required")
        if not isinstance(self.internal_device, bool):
            raise ValueError("USB internal_device must be boolean")

    @property
    def complete(self) -> bool:
        return self.state == UsbObservationState.OBSERVED and (
            self.connector_type.casefold() not in {"usb-c", "usb-c/dock"} or bool(self.orientation)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "physical_label": self.physical_label, "logical_port": self.logical_port,
            "connector_type": self.connector_type, "tested_speed": self.tested_speed,
            "controller_id": self.controller_id, "orientation": self.orientation,
            "internal_device": self.internal_device, "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "UsbPortObservation":
        if not isinstance(data, dict):
            raise ValueError("USB port observation must be a mapping")
        required = {"physical_label", "logical_port", "connector_type", "tested_speed", "controller_id", "orientation", "internal_device", "state"}
        if set(data) != required:
            raise ValueError("USB port observation has missing or unknown fields")
        return cls(
            physical_label=str(data["physical_label"]), logical_port=str(data["logical_port"]),
            connector_type=str(data["connector_type"]), tested_speed=str(data["tested_speed"]),
            controller_id=str(data["controller_id"]), orientation=data["orientation"],
            internal_device=data["internal_device"], state=UsbObservationState(str(data["state"])),
        )


@dataclass(frozen=True)
class UsbEvidenceSession:
    snapshot_id: str
    bios_binding: str
    private_ref: str
    capture_version: str
    observations: Tuple[UsbPortObservation, ...] = field(default_factory=tuple)
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1", "snapshot_id": self.snapshot_id, "bios_binding": self.bios_binding,
            "private_ref": self.private_ref, "capture_version": self.capture_version,
            "observations": [item.to_dict() for item in self.observations], "confidence": self.confidence.value,
        }

    @property
    def completeness(self) -> Tuple[EvidenceCompleteness, Tuple[str, ...]]:
        if not self.observations:
            return EvidenceCompleteness.MISSING, ("no physical USB port observations",)
        labels = [item.physical_label.casefold() for item in self.observations]
        logical = [item.logical_port.casefold() for item in self.observations]
        if len(set(labels)) != len(labels) or len(set(logical)) != len(logical):
            return EvidenceCompleteness.CONFLICTING, ("duplicate physical or logical USB port",)
        unresolved = tuple(
            f"{item.physical_label}: physical test incomplete"
            for item in self.observations if not item.complete
        )
        if unresolved:
            return EvidenceCompleteness.PARTIAL, unresolved
        return EvidenceCompleteness.COMPLETE, ()

    def to_evidence_record(self) -> EvidenceRecord:
        status, unresolved = self.completeness
        digest = canonical_json_digest(self.to_dict())
        return EvidenceRecord(
            evidence_id=f"usb-session-{self.snapshot_id}", kind="usb", schema_version="1", digest=digest,
            private_ref=self.private_ref, machine_snapshot_id=self.snapshot_id, bios_binding=self.bios_binding,
            capture_method="manual-physical-port-session", capture_version=self.capture_version,
            completeness=status, confidence=self.confidence, unresolved_checks=unresolved,
            physical_port_evidence=status == EvidenceCompleteness.COMPLETE,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "UsbEvidenceSession":
        if not isinstance(data, dict):
            raise ValueError("USB evidence session must be a mapping")
        required = {"schema_version", "snapshot_id", "bios_binding", "private_ref", "capture_version", "observations", "confidence"}
        if set(data) != required or data["schema_version"] != "1":
            raise ValueError("USB evidence session has an invalid schema")
        if not isinstance(data["observations"], list):
            raise ValueError("USB evidence observations must be a list")
        return cls(
            snapshot_id=str(data["snapshot_id"]), bios_binding=str(data["bios_binding"]),
            private_ref=str(data["private_ref"]), capture_version=str(data["capture_version"]),
            observations=tuple(UsbPortObservation.from_dict(item) for item in data["observations"]),
            confidence=EvidenceConfidence(str(data["confidence"])),
        )
