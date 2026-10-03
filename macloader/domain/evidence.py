"""Evidence contracts used by configuration review and acceptance gates."""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, Tuple


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceCompleteness(str, Enum):
    MISSING = "missing"
    PARTIAL = "partial"
    COMPLETE = "complete"
    CONFLICTING = "conflicting"
    STALE = "stale"


class EvidenceConfidence(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class EvidenceRecord:
    """Sanitized metadata for private evidence held outside public exports."""

    evidence_id: str
    kind: str
    schema_version: str
    digest: str
    private_ref: str
    machine_snapshot_id: str
    bios_binding: str
    capture_method: str
    capture_version: str
    completeness: EvidenceCompleteness
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    unresolved_checks: Tuple[str, ...] = field(default_factory=tuple)
    physical_port_evidence: bool = False
    input_scope: Tuple[str, ...] = field(default_factory=tuple)
    input_digest: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.kind.strip():
            raise ValueError("evidence identity is required")
        if not _DIGEST_RE.fullmatch(self.digest.lower()):
            raise ValueError("evidence digest must be a lowercase SHA-256 digest")
        if not self.private_ref.strip():
            raise ValueError("evidence private_ref is required")
        if not all(isinstance(item, str) and item for item in self.input_scope):
            raise ValueError("evidence input scopes must be strings")
        if self.input_scope and not _DIGEST_RE.fullmatch(self.input_digest):
            raise ValueError("scoped evidence requires an input digest")
        if not isinstance(self.physical_port_evidence, bool):
            raise ValueError("physical_port_evidence must be boolean")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "schema_version": self.schema_version,
            "digest": self.digest.lower(),
            "private_ref": self.private_ref,
            "machine_snapshot_id": self.machine_snapshot_id,
            "bios_binding": self.bios_binding,
            "capture_method": self.capture_method,
            "capture_version": self.capture_version,
            "completeness": self.completeness.value,
            "confidence": self.confidence.value,
            "unresolved_checks": list(self.unresolved_checks),
            "physical_port_evidence": self.physical_port_evidence,
            "input_scope": list(self.input_scope), "input_digest": self.input_digest,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceRecord":
        if not isinstance(data, dict):
            raise ValueError("evidence record must be a mapping")
        required = {
            "evidence_id", "kind", "schema_version", "digest", "private_ref",
            "machine_snapshot_id", "bios_binding", "capture_method", "capture_version",
            "completeness", "confidence", "unresolved_checks", "physical_port_evidence",
        }
        if not required <= set(data) or set(data) - required - {"input_scope", "input_digest"}:
            raise ValueError("evidence record has missing or unknown fields")
        checks = data["unresolved_checks"]
        if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
            raise ValueError("evidence unresolved_checks must be a list of strings")
        if not isinstance(data.get("input_scope", []), list):
            raise ValueError("evidence input scope must be a list")
        return cls(
            evidence_id=str(data["evidence_id"]), kind=str(data["kind"]),
            schema_version=str(data["schema_version"]), digest=str(data["digest"]),
            private_ref=str(data["private_ref"]), machine_snapshot_id=str(data["machine_snapshot_id"]),
            bios_binding=str(data["bios_binding"]), capture_method=str(data["capture_method"]),
            capture_version=str(data["capture_version"]),
            completeness=EvidenceCompleteness(str(data["completeness"])),
            confidence=EvidenceConfidence(str(data["confidence"])),
            unresolved_checks=tuple(checks),
            physical_port_evidence=data["physical_port_evidence"],
            input_scope=tuple(data.get("input_scope", [])), input_digest=str(data.get("input_digest", "")),
        )
