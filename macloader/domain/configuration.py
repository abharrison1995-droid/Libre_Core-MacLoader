"""Immutable configuration, confirmation, and issue contracts."""

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Dict, Iterable, Optional, Tuple
import uuid

from macloader.domain.contracts import IdentityReference, canonical_json_digest
from macloader.domain.evidence import EvidenceRecord
from macloader.domain.targets import MacOsTarget


CONFIGURATION_SCHEMA_VERSION = "1"


class ObservationStatus(str, Enum):
    OBSERVED = "observed"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"
    FAILED = "failed"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class BlockingStage(str, Enum):
    NONE = "none"
    CONFIGURATION = "configuration"
    BUILD = "build"
    VALIDATION = "validation"
    MEDIA = "media"
    ACCEPTANCE = "acceptance"


@dataclass(frozen=True)
class HardwareObservation:
    field_path: str
    value: str
    source: str
    provider_version: str
    evidence_ref: Optional[str] = None
    status: ObservationStatus = ObservationStatus.OBSERVED
    confidence: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_path": self.field_path, "value": self.value, "source": self.source,
            "provider_version": self.provider_version, "evidence_ref": self.evidence_ref,
            "status": self.status.value, "confidence": self.confidence,
        }


@dataclass(frozen=True)
class HardwareConfirmation:
    field_path: str
    action: str
    effective_value: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "field_path": self.field_path, "action": self.action,
            "effective_value": self.effective_value, "reason": self.reason,
        }


@dataclass(frozen=True)
class Acknowledgement:
    rule_id: str
    warning_digest: str
    configuration_digest: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "rule_id": self.rule_id, "warning_digest": self.warning_digest,
            "configuration_digest": self.configuration_digest,
        }


@dataclass(frozen=True)
class RecoveryResolution:
    requested_product: str
    requested_version: str
    requested_build: str
    resolved_product: Optional[str] = None
    resolved_version: Optional[str] = None
    resolved_build: Optional[str] = None
    integrity_digest: Optional[str] = None
    relationship_policy: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested_product": self.requested_product, "requested_version": self.requested_version,
            "requested_build": self.requested_build, "resolved_product": self.resolved_product,
            "resolved_version": self.resolved_version, "resolved_build": self.resolved_build,
            "integrity_digest": self.integrity_digest, "relationship_policy": self.relationship_policy,
        }


@dataclass(frozen=True)
class ConfigurationIssue:
    code: str
    field_path: str
    severity: IssueSeverity
    blocking_stage: BlockingStage
    rule_id: str
    explanation: str
    remediation: str

    @property
    def blocking(self) -> bool:
        return self.blocking_stage != BlockingStage.NONE and self.severity == IssueSeverity.ERROR

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code, "field_path": self.field_path, "severity": self.severity.value,
            "blocking_stage": self.blocking_stage.value, "rule_id": self.rule_id,
            "explanation": self.explanation, "remediation": self.remediation,
        }


def _sorted_pairs(values: Iterable[Tuple[str, str]]) -> Tuple[Tuple[str, str], ...]:
    pairs = tuple(values)
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in pairs):
        raise ValueError("configuration option selections must be string pairs")
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("configuration option selections must not contain duplicate IDs")
    return tuple(sorted(pairs))


@dataclass(frozen=True)
class UserConfiguration:
    """A serializable draft. Completeness is evaluated against current policy."""

    schema_version: str = CONFIGURATION_SCHEMA_VERSION
    configuration_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    revision: int = 0
    hardware_snapshot_id: str = ""
    hardware_snapshot_digest: str = ""
    target: Optional[MacOsTarget] = None
    option_selections: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    observations: Tuple[HardwareObservation, ...] = field(default_factory=tuple)
    confirmations: Tuple[HardwareConfirmation, ...] = field(default_factory=tuple)
    evidence: Tuple[EvidenceRecord, ...] = field(default_factory=tuple)
    recovery: Optional[RecoveryResolution] = None
    identity_ref: Optional[IdentityReference] = None
    acknowledgements: Tuple[Acknowledgement, ...] = field(default_factory=tuple)
    policy_version: str = ""
    # Workflow-only CAS metadata. It is not part of the persisted/public
    # schema; each loaded draft owns the base revision it may replace.
    loaded_base_revision: Optional[int] = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != CONFIGURATION_SCHEMA_VERSION:
            raise ValueError(f"Unsupported configuration schema: {self.schema_version}")
        if self.revision < 0:
            raise ValueError("configuration revision cannot be negative")
        object.__setattr__(self, "option_selections", _sorted_pairs(self.option_selections))
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("configuration evidence IDs must be unique")

    def selected_options(self) -> Dict[str, str]:
        return dict(self.option_selections)

    def semantic_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hardware_snapshot_id": self.hardware_snapshot_id,
            "hardware_snapshot_digest": self.hardware_snapshot_digest,
            "target": self.target.to_dict() if self.target else None,
            "option_selections": [[key, value] for key, value in self.option_selections],
            "observations": [item.to_dict() for item in self.observations],
            "confirmations": [item.to_dict() for item in self.confirmations],
            "evidence": [item.to_dict() for item in self.evidence],
            "recovery": self.recovery.to_dict() if self.recovery else None,
            "identity_ref": self.identity_ref.to_dict() if self.identity_ref else None,
            "acknowledgements": [item.to_dict() for item in self.acknowledgements],
            "policy_version": self.policy_version,
        }

    @property
    def semantic_digest(self) -> str:
        return canonical_json_digest(self.semantic_dict())

    def to_dict(self) -> Dict[str, Any]:
        data = self.semantic_dict()
        data.update({"configuration_id": self.configuration_id, "revision": self.revision})
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Any) -> "UserConfiguration":
        if not isinstance(data, dict):
            raise ValueError("configuration must be a mapping")
        expected = {
            "schema_version", "configuration_id", "revision", "hardware_snapshot_id",
            "hardware_snapshot_digest", "target", "option_selections", "observations",
            "confirmations", "evidence", "recovery", "identity_ref", "acknowledgements",
            "policy_version",
        }
        if set(data) != expected:
            raise ValueError("configuration has missing or unknown fields")
        schema = data.get("schema_version")
        if schema != CONFIGURATION_SCHEMA_VERSION:
            raise ValueError(f"Unsupported configuration schema: {schema}")
        options = data.get("option_selections", [])
        if not isinstance(options, list) or not all(isinstance(item, list) and len(item) == 2 for item in options):
            raise ValueError("option_selections must be a list of [id, value] pairs")
        for field_name in ("observations", "confirmations", "evidence", "acknowledgements"):
            if not isinstance(data[field_name], list):
                raise ValueError(f"{field_name} must be a list")
        observations = tuple(_observation_from_dict(item) for item in data["observations"])
        confirmations = tuple(_confirmation_from_dict(item) for item in data["confirmations"])
        evidence = tuple(EvidenceRecord.from_dict(item) for item in data["evidence"])
        acknowledgements = tuple(_ack_from_dict(item) for item in data["acknowledgements"])
        target = MacOsTarget.from_dict(data["target"]) if data.get("target") is not None else None
        recovery_data = data.get("recovery")
        if recovery_data is not None and not isinstance(recovery_data, dict):
            raise ValueError("recovery must be a mapping or null")
        recovery = RecoveryResolution(**recovery_data) if isinstance(recovery_data, dict) else None
        identity_data = data.get("identity_ref")
        if identity_data is not None and not isinstance(identity_data, dict):
            raise ValueError("identity_ref must be a mapping or null")
        if isinstance(identity_data, dict) and set(identity_data) != {"schema_version", "storage_ref", "redacted"}:
            raise ValueError("identity_ref has missing or unknown fields")
        identity = IdentityReference(**identity_data) if isinstance(identity_data, dict) else None
        return cls(
            schema_version=schema, configuration_id=str(data.get("configuration_id") or uuid.uuid4()),
            revision=int(data.get("revision", 0)), hardware_snapshot_id=str(data.get("hardware_snapshot_id", "")),
            hardware_snapshot_digest=str(data.get("hardware_snapshot_digest", "")), target=target,
            option_selections=tuple((str(item[0]), str(item[1])) for item in options),
            observations=observations, confirmations=confirmations, evidence=evidence,
            recovery=recovery, identity_ref=identity,
            acknowledgements=acknowledgements, policy_version=str(data.get("policy_version", "")),
        )


@dataclass(frozen=True)
class AcceptedConfiguration:
    """A configuration accepted only after re-evaluation against current policy."""

    configuration: UserConfiguration
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.semantic_digest != self.configuration.semantic_digest:
            raise ValueError("accepted configuration digest does not match its content")

    def to_dict(self) -> Dict[str, Any]:
        return self.configuration.to_dict()


def _observation_from_dict(data: Any) -> HardwareObservation:
    if not isinstance(data, dict):
        raise ValueError("hardware observation must be a mapping")
    return HardwareObservation(
        field_path=str(data["field_path"]), value=str(data["value"]), source=str(data["source"]),
        provider_version=str(data["provider_version"]), evidence_ref=data.get("evidence_ref"),
        status=ObservationStatus(str(data.get("status", ObservationStatus.OBSERVED.value))),
        confidence=str(data.get("confidence", "unknown")),
    )


def _confirmation_from_dict(data: Any) -> HardwareConfirmation:
    if not isinstance(data, dict):
        raise ValueError("hardware confirmation must be a mapping")
    return HardwareConfirmation(
        field_path=str(data["field_path"]), action=str(data["action"]),
        effective_value=str(data["effective_value"]), reason=str(data["reason"]),
    )


def _ack_from_dict(data: Any) -> Acknowledgement:
    if not isinstance(data, dict):
        raise ValueError("acknowledgement must be a mapping")
    return Acknowledgement(
        rule_id=str(data["rule_id"]), warning_digest=str(data["warning_digest"]),
        configuration_digest=str(data["configuration_digest"]),
    )
