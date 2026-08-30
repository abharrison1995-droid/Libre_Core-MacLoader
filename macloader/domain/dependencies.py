"""Domain models for OpenCore dependencies, catalog specs, and resolution sets."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Dict, List, Optional


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
        """Retrieve artifact for the requested variant, falling back to RELEASE if DEBUG is unavailable."""
        return self.artifacts.get(variant.value) or self.artifacts.get(ArtifactVariant.RELEASE.value)

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
    resolved_dependencies: List[ResolvedDependency] = field(default_factory=list)
    unresolved_requirements: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    is_complete: bool = True
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_model": self.target_model,
            "target_macos": self.target_macos,
            "policy_version": self.policy_version,
            "variant": self.variant.value,
            "resolved_dependencies": [d.to_dict() for d in self.resolved_dependencies],
            "unresolved_requirements": list(self.unresolved_requirements),
            "warnings": list(self.warnings),
            "is_complete": self.is_complete,
            "timestamp": self.timestamp,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResolvedDependencySet":
        return cls(
            target_model=data["target_model"],
            target_macos=data["target_macos"],
            policy_version=data["policy_version"],
            variant=ArtifactVariant(data["variant"]),
            resolved_dependencies=[
                ResolvedDependency.from_dict(d) for d in data.get("resolved_dependencies", [])
            ],
            unresolved_requirements=list(data.get("unresolved_requirements", [])),
            warnings=list(data.get("warnings", [])),
            is_complete=bool(data.get("is_complete", True)),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )
