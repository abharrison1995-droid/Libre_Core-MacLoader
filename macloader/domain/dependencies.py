"""Domain models for OpenCore dependencies, catalog specs, and resolution sets."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Dict, List, Optional
from macloader.domain.contracts import ArtifactLock, ArtifactLockEntry, CONTRACT_SCHEMA_VERSION, canonical_json_digest


class ArtifactVariant(str, Enum):
    """Artifact release flavor."""
    RELEASE = "RELEASE"
    DEBUG = "DEBUG"


@dataclass
class DependencyArtifact:
    """Represents a downloadable binary artifact with expected checksum."""
    asset_name: str
    source_url: str
    sha256: str
    size_bytes: int
    variant: ArtifactVariant = ArtifactVariant.RELEASE
    archive_type: str = "zip"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_name": self.asset_name,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "variant": self.variant.value,
            "archive_type": self.archive_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DependencyArtifact":
        return cls(
            asset_name=data["asset_name"],
            source_url=data["source_url"],
            sha256=data["sha256"].lower(),
            size_bytes=int(data.get("size_bytes", 0)),
            variant=ArtifactVariant(data.get("variant", "RELEASE")),
            archive_type=data.get("archive_type", "zip"),
        )


@dataclass
class DependencySpec:
    """Specification of an upstream project dependency and its available release artifacts."""
    id: str  # Stable canonical ID, e.g. "lilu", "whatevergreen"
    project_name: str
    upstream_repository: str
    license: str
    version: str
    release_tag: str
    dependencies: List[str] = field(default_factory=list)  # Parent dependency IDs
    artifacts: Dict[str, DependencyArtifact] = field(default_factory=dict)  # variant -> artifact
    subcomponents: List[str] = field(default_factory=list)  # e.g. specific kexts/plugins
    date_verified: str = ""

    def get_artifact(self, variant: ArtifactVariant = ArtifactVariant.RELEASE) -> Optional[DependencyArtifact]:
        """Retrieve exactly the requested artifact variant."""
        return self.artifacts.get(variant.value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_name": self.project_name,
            "upstream_repository": self.upstream_repository,
            "license": self.license,
            "version": self.version,
            "release_tag": self.release_tag,
            "dependencies": list(self.dependencies),
            "artifacts": {k: v.to_dict() for k, v in self.artifacts.items()},
            "subcomponents": list(self.subcomponents),
            "date_verified": self.date_verified,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DependencySpec":
        artifacts = {}
        for k, v in data.get("artifacts", {}).items():
            artifacts[k] = DependencyArtifact.from_dict(v)
        return cls(
            id=data["id"].lower(),
            project_name=data["project_name"],
            upstream_repository=data["upstream_repository"],
            license=data["license"],
            version=data["version"],
            release_tag=data["release_tag"],
            dependencies=[d.lower() for d in data.get("dependencies", [])],
            artifacts=artifacts,
            subcomponents=list(data.get("subcomponents", [])),
            date_verified=data.get("date_verified", ""),
        )


@dataclass
class ResolvedDependency:
    """An individual resolved dependency explaining why and how it was selected."""
    dependency_id: str
    project_name: str
    version: str
    variant: ArtifactVariant
    artifact: DependencyArtifact
    reason: str
    required_by: str
    is_transitive: bool = False
    subcomponents: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "project_name": self.project_name,
            "version": self.version,
            "variant": self.variant.value,
            "artifact": self.artifact.to_dict(),
            "reason": self.reason,
            "required_by": self.required_by,
            "is_transitive": self.is_transitive,
            "subcomponents": list(self.subcomponents),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResolvedDependency":
        return cls(
            dependency_id=data["dependency_id"],
            project_name=data["project_name"],
            version=data["version"],
            variant=ArtifactVariant(data["variant"]),
            artifact=DependencyArtifact.from_dict(data["artifact"]),
            reason=data["reason"],
            required_by=data["required_by"],
            is_transitive=bool(data.get("is_transitive", False)),
            subcomponents=list(data.get("subcomponents", [])),
        )


@dataclass
class ResolvedDependencySet:
    """Complete, topologically ordered dependency set for a BuildPlan."""
    target_model: str
    target_macos: str
    policy_version: str
    variant: ArtifactVariant
    catalog_digest: str = ""
    resolved_dependencies: List[ResolvedDependency] = field(default_factory=list)
    unresolved_requirements: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    is_complete: bool = True
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    plan_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_model": self.target_model,
            "target_macos": self.target_macos,
            "policy_version": self.policy_version,
            "plan_digest": self.plan_digest,
            "catalog_digest": self.catalog_digest,
            "variant": self.variant.value,
            "resolved_dependencies": [d.to_dict() for d in self.resolved_dependencies],
            "unresolved_requirements": list(self.unresolved_requirements),
            "warnings": list(self.warnings),
            "is_complete": self.is_complete,
            "timestamp": self.timestamp,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def canonical_digest(self) -> str:
        data = self.to_dict()
        data.pop("timestamp", None)
        return canonical_json_digest(data)

    def to_artifact_lock(self) -> ArtifactLock:
        return ArtifactLock(
            schema_version=CONTRACT_SCHEMA_VERSION,
            policy_version=self.policy_version,
            catalog_digest=self.catalog_digest,
            plan_digest=self.plan_digest,
            entries=tuple(
                ArtifactLockEntry(
                    dependency_id=item.dependency_id, version=item.version, variant=item.variant.value,
                    asset_name=item.artifact.asset_name, source_url=item.artifact.source_url,
                    sha256=item.artifact.sha256, size_bytes=item.artifact.size_bytes,
                    archive_type=item.artifact.archive_type,
                    subcomponents=tuple(item.subcomponents),
                ) for item in self.resolved_dependencies
            ),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResolvedDependencySet":
        catalog_digest = data.get("catalog_digest")
        policy_version = data.get("policy_version")
        if not isinstance(catalog_digest, str) or not catalog_digest.strip():
            raise ValueError("Resolved dependency set requires a non-empty catalog_digest")
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise ValueError("Resolved dependency set requires a non-empty policy_version")
        plan_digest = data.get("plan_digest", "")
        if not isinstance(plan_digest, str) or not plan_digest.strip():
            raise ValueError("Resolved dependency set requires a non-empty plan_digest; re-resolve this legacy lock")
        is_complete = data.get("is_complete", True)
        if not isinstance(is_complete, bool):
            raise ValueError("Resolved dependency set is_complete must be a boolean")
        unresolved_requirements = data.get("unresolved_requirements", [])
        if not isinstance(unresolved_requirements, list) or not all(isinstance(item, str) for item in unresolved_requirements):
            raise ValueError("Resolved dependency set unresolved_requirements must be a list of strings")
        if is_complete != (not unresolved_requirements):
            raise ValueError("Resolved dependency set is_complete does not match unresolved_requirements")
        return cls(
            target_model=data["target_model"],
            target_macos=data["target_macos"],
            policy_version=policy_version,
            plan_digest=plan_digest,
            catalog_digest=catalog_digest,
            variant=ArtifactVariant(data["variant"]),
            resolved_dependencies=[
                ResolvedDependency.from_dict(d) for d in data.get("resolved_dependencies", [])
            ],
            unresolved_requirements=list(unresolved_requirements),
            warnings=list(data.get("warnings", [])),
            is_complete=is_complete,
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )
