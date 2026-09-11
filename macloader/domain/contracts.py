"""Versioned contracts shared by planning, building, and validation stages."""

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple


CONTRACT_SCHEMA_VERSION = "0.1"


def canonical_json_digest(value: Any) -> str:
    """Compute deterministic SHA-256 hex digest of a JSON-serializable value using compact sorted keys."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


_digest = canonical_json_digest



@dataclass(frozen=True)
class ArtifactLockEntry:
    dependency_id: str
    version: str
    variant: str
    asset_name: str
    source_url: str
    sha256: str
    size_bytes: int
    archive_type: str = "zip"
    subcomponents: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dependency_id": self.dependency_id, "version": self.version, "variant": self.variant,
            "asset_name": self.asset_name, "source_url": self.source_url, "sha256": self.sha256,
            "size_bytes": self.size_bytes, "archive_type": self.archive_type,
            "subcomponents": list(self.subcomponents),
        }


@dataclass(frozen=True)
class ArtifactLock:
    schema_version: str
    policy_version: str
    catalog_digest: str
    entries: Tuple[ArtifactLockEntry, ...] = field(default_factory=tuple)
    plan_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version, "policy_version": self.policy_version,
            "catalog_digest": self.catalog_digest, "plan_digest": self.plan_digest,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class ToolchainSelection:
    schema_version: str
    opencore_version: str
    ocvalidate_version: str
    acpi_compiler: Optional[str]
    identity_tool: Optional[str]
    recovery_tool: Optional[str]
    host_platform: str
    host_architecture: str
    provenance: Dict[str, str] = field(default_factory=dict)
    ocvalidate_path: Optional[str] = None
    ocvalidate_sha256: Optional[str] = None
    acpi_compiler_path: Optional[str] = None
    acpi_compiler_sha256: Optional[str] = None
    identity_tool_path: Optional[str] = None
    identity_tool_sha256: Optional[str] = None
    sample_plist_path: Optional[str] = None
    sample_plist_sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version, "opencore_version": self.opencore_version,
            "ocvalidate_version": self.ocvalidate_version, "acpi_compiler": self.acpi_compiler,
            "identity_tool": self.identity_tool, "recovery_tool": self.recovery_tool,
            "host_platform": self.host_platform, "host_architecture": self.host_architecture,
            "provenance": dict(self.provenance), "ocvalidate_path": self.ocvalidate_path,
            "ocvalidate_sha256": self.ocvalidate_sha256,
            "acpi_compiler_path": self.acpi_compiler_path,
            "acpi_compiler_sha256": self.acpi_compiler_sha256,
            "identity_tool_path": self.identity_tool_path,
            "identity_tool_sha256": self.identity_tool_sha256,
            "sample_plist_path": self.sample_plist_path,
            "sample_plist_sha256": self.sample_plist_sha256,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class IdentityReference:
    schema_version: str
    storage_ref: str
    redacted: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": self.schema_version, "storage_ref": self.storage_ref, "redacted": self.redacted}


@dataclass(frozen=True)
class ValidationReport:
    schema_version: str
    status: str
    validator_version: Optional[str]
    checks: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version, "status": self.status,
            "validator_version": self.validator_version, "checks": dict(self.checks),
            "errors": list(self.errors), "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class BuildManifest:
    schema_version: str
    build_digest: str
    target_model: str
    target_macos: str
    artifact_lock_digest: str
    validation_report: str
    output_paths: Dict[str, str] = field(default_factory=dict)
    toolchain_digest: str = ""
    identity_digest: str = ""
    output_digest: str = ""
    license_digests: Dict[str, str] = field(default_factory=dict)
    schema_digest: str = ""
    profile_digest: str = ""
    acpi_digest: str = ""
    evidence_digests: Tuple[str, ...] = field(default_factory=tuple)
    usb_policy_state: str = ""
    usb_first_install_route: str = ""
    identity_reference: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version, "build_digest": self.build_digest,
            "target_model": self.target_model, "target_macos": self.target_macos,
            "artifact_lock_digest": self.artifact_lock_digest, "validation_report": self.validation_report,
            "toolchain_digest": self.toolchain_digest, "identity_digest": self.identity_digest,
            "output_digest": self.output_digest,
            "license_digests": dict(self.license_digests),
            "schema_digest": self.schema_digest,
            "profile_digest": self.profile_digest,
            "acpi_digest": self.acpi_digest,
            "evidence_digests": list(self.evidence_digests),
            "usb_policy_state": self.usb_policy_state,
            "usb_first_install_route": self.usb_first_install_route,
            "identity_reference": self.identity_reference,
            "output_paths": dict(self.output_paths),
        }
