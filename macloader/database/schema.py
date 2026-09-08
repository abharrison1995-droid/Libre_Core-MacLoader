"""Schema definitions and validation logic for the MacLoader declarative database."""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from macloader.domain.compatibility import CompatibilityState
from macloader.domain.dependencies import ArtifactVariant, DependencyArtifact, DependencySpec
from macloader.exceptions import DatabaseValidationError


@dataclass
class ModelSchema:
    """Schema for a supported laptop model definition."""
    id: str
    vendor: str
    family: str
    display_name: str
    machine_types: List[str]
    product_names: List[str]
    cpu_generations: List[str]
    default_macos_status: Dict[str, CompatibilityState]
    known_components: Dict[str, List[str]] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def validate_and_load(cls, data: Dict[str, Any], filename: str = "") -> "ModelSchema":
        if not isinstance(data, dict):
            raise DatabaseValidationError(f"Model definition in {filename} must be a YAML mapping")

        for req in ("id", "vendor", "family", "display_name", "machine_types", "product_names", "cpu_generations"):
            if req not in data:
                raise DatabaseValidationError(f"Missing required field '{req}' in model definition {filename}")

        default_status: Dict[str, CompatibilityState] = {}
        status_map = data.get("default_macos_status", {})
        if not isinstance(status_map, dict):
            raise DatabaseValidationError(f"'default_macos_status' in {filename} must be a dictionary")

        for os_name, state_str in status_map.items():
            try:
                default_status[os_name.lower()] = CompatibilityState(state_str)
            except ValueError:
                raise DatabaseValidationError(
                    f"Invalid compatibility state '{state_str}' for OS '{os_name}' in {filename}"
                )

        return cls(
            id=str(data["id"]),
            vendor=str(data["vendor"]),
            family=str(data["family"]),
            display_name=str(data["display_name"]),
            machine_types=[str(mt).upper() for mt in data["machine_types"]],
            product_names=[str(pn) for pn in data["product_names"]],
            cpu_generations=[str(gen) for gen in data["cpu_generations"]],
            default_macos_status=default_status,
            known_components=dict(data.get("known_components", {})),
            options=dict(data.get("options", {})),
        )


@dataclass
class ComponentVersionPolicy:
    """Compatibility policy for a component on a specific macOS release."""
    state: CompatibilityState
    reason: str
    required_actions: List[str] = field(default_factory=list)
    known_limitations: List[str] = field(default_factory=list)
    unresolved_requirements: List[str] = field(default_factory=list)


@dataclass
class ComponentSchema:
    """Schema for a hardware component definition and its version policies."""
    id: str
    category: str
    name: str
    match_rules: Dict[str, Any]
    macos_policies: Dict[str, ComponentVersionPolicy]

    @classmethod
    def validate_and_load(cls, data: Dict[str, Any], filename: str = "") -> "ComponentSchema":
        if not isinstance(data, dict):
            raise DatabaseValidationError(f"Component definition in {filename} must be a YAML mapping")

        for req in ("id", "category", "name", "match_rules", "macos_policies"):
            if req not in data:
                raise DatabaseValidationError(f"Missing required field '{req}' in component definition {filename}")

        policies: Dict[str, ComponentVersionPolicy] = {}
        raw_policies = data["macos_policies"]
        if not isinstance(raw_policies, dict):
            raise DatabaseValidationError(f"'macos_policies' in {filename} must be a mapping of OS to policy")

        for os_name, pol_data in raw_policies.items():
            if not isinstance(pol_data, dict):
                raise DatabaseValidationError(f"Policy for '{os_name}' in {filename} must be a dictionary")
            if "state" not in pol_data:
                raise DatabaseValidationError(f"Missing 'state' in policy for '{os_name}' in {filename}")

            try:
                state = CompatibilityState(pol_data["state"])
            except ValueError:
                raise DatabaseValidationError(
                    f"Invalid compatibility state '{pol_data['state']}' for OS '{os_name}' in {filename}"
                )

            policies[os_name.lower()] = ComponentVersionPolicy(
                state=state,
                reason=pol_data.get("reason", ""),
                required_actions=list(pol_data.get("required_actions", [])),
                known_limitations=list(pol_data.get("known_limitations", [])),
                unresolved_requirements=list(pol_data.get("unresolved_requirements", [])),
            )

        return cls(
            id=str(data["id"]),
            category=str(data["category"]),
            name=str(data["name"]),
            match_rules=dict(data["match_rules"]),
            macos_policies=policies,
        )


@dataclass
class MacOsSchema:
    """Schema for a supported macOS release."""
    id: str  # e.g. "sequoia"
    version_name: str  # e.g. "macOS 15 Sequoia"
    major_version: int
    min_opencore_version: str
    status: CompatibilityState
    notes: List[str] = field(default_factory=list)

    @classmethod
    def validate_and_load(cls, data: Dict[str, Any], filename: str = "") -> "MacOsSchema":
        if not isinstance(data, dict):
            raise DatabaseValidationError(f"macOS definition in {filename} must be a YAML mapping")

        for req in ("id", "version_name", "major_version", "min_opencore_version", "status"):
            if req not in data:
                raise DatabaseValidationError(f"Missing required field '{req}' in macOS definition {filename}")

        try:
            state = CompatibilityState(data["status"])
        except ValueError:
            raise DatabaseValidationError(f"Invalid status '{data['status']}' in macOS definition {filename}")

        return cls(
            id=str(data["id"]).lower(),
            version_name=str(data["version_name"]),
            major_version=int(data["major_version"]),
            min_opencore_version=str(data["min_opencore_version"]),
            status=state,
            notes=list(data.get("notes", [])),
        )


@dataclass
class DependencyCatalogSchema:
    """Schema for the versioned dependency catalog."""
    policy_version: str
    dependencies: Dict[str, DependencySpec]

    @classmethod
    def validate_and_load(cls, data: Dict[str, Any], filename: str = "") -> "DependencyCatalogSchema":
        if not isinstance(data, dict):
            raise DatabaseValidationError(f"Dependency catalog in {filename} must be a YAML mapping")

        if "macloader_dependency_set" not in data:
            raise DatabaseValidationError(f"Missing 'macloader_dependency_set' in {filename}")

        policy_ver = str(data["macloader_dependency_set"])
        raw_deps = data.get("dependencies", [])
        if not isinstance(raw_deps, list):
            raise DatabaseValidationError(f"'dependencies' in {filename} must be a list of dependency specifications")

        dep_specs: Dict[str, DependencySpec] = {}
        declared_ids = [str(item.get("id", "")).lower() for item in raw_deps if isinstance(item, dict)]
        duplicates = sorted({dep_id for dep_id in declared_ids if declared_ids.count(dep_id) > 1})
        if duplicates:
            raise DatabaseValidationError(f"Duplicate dependency ID '{duplicates[0]}' in {filename}")
        for item in raw_deps:
            if not isinstance(item, dict):
                raise DatabaseValidationError(f"Item in 'dependencies' list of {filename} must be a mapping")

            for req in ("id", "project_name", "upstream_repository", "license", "version", "release_tag", "artifacts"):
                if req not in item:
                    raise DatabaseValidationError(f"Missing required field '{req}' in dependency definition in {filename}")

            for field_name in ("project_name", "upstream_repository", "license", "version", "release_tag"):
                if not isinstance(item[field_name], str) or not item[field_name].strip():
                    raise DatabaseValidationError(f"Dependency field '{field_name}' must be a non-empty string in {filename}")
            for field_name in ("version", "release_tag"):
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]*", item[field_name]) or ".." in item[field_name]:
                    raise DatabaseValidationError(f"Unsafe {field_name} in dependency definition in {filename}")
            if not isinstance(item.get("dependencies", []), list) or not all(isinstance(value, str) for value in item.get("dependencies", [])):
                raise DatabaseValidationError(f"'dependencies' for '{item.get('id', '')}' must be a list of strings")
            if not isinstance(item.get("subcomponents", []), list) or not all(isinstance(value, str) for value in item.get("subcomponents", [])):
                raise DatabaseValidationError(f"'subcomponents' for '{item.get('id', '')}' must be a list of strings")

            dep_id = str(item["id"]).lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", dep_id):
                raise DatabaseValidationError(f"Invalid dependency ID '{dep_id}' in {filename}")
            raw_artifacts = item["artifacts"]
            if not isinstance(raw_artifacts, dict) or not raw_artifacts:
                raise DatabaseValidationError(f"'artifacts' for dependency '{dep_id}' in {filename} must be a non-empty mapping")
            normalized_variant_keys = [str(key).upper() for key in raw_artifacts]
            if len(normalized_variant_keys) != len(set(normalized_variant_keys)):
                raise DatabaseValidationError(f"Duplicate artifact variants for dependency '{dep_id}' in {filename}")

            artifacts: Dict[str, DependencyArtifact] = {}
            for var_key, art_data in raw_artifacts.items():
                if str(var_key).upper() not in {"RELEASE", "DEBUG"}:
                    raise DatabaseValidationError(f"Unsupported artifact variant '{var_key}' in '{dep_id}'")
                if not isinstance(art_data, dict):
                    raise DatabaseValidationError(f"Artifact for variant '{var_key}' in '{dep_id}' must be a mapping")
                for areq in ("asset_name", "source_url", "sha256"):
                    if areq not in art_data:
                        raise DatabaseValidationError(f"Missing '{areq}' in artifact '{var_key}' of '{dep_id}' in {filename}")

                sha_val = str(art_data["sha256"]).lower().strip()
                if not re.fullmatch(r"[0-9a-f]{64}", sha_val):
                    raise DatabaseValidationError(
                        f"Invalid SHA-256 hash '{sha_val}' for artifact '{var_key}' of '{dep_id}' in {filename}. Must be 64-char lowercase hex."
                    )

                parsed_url = urlparse(str(art_data["source_url"]))
                repo_url = urlparse(str(item["upstream_repository"]))
                if parsed_url.scheme != "https" or parsed_url.hostname != "github.com" or parsed_url.port not in (None, 443) or parsed_url.username or parsed_url.password:
                    raise DatabaseValidationError(f"Artifact '{var_key}' of '{dep_id}' must use an HTTPS GitHub URL on port 443")
                if repo_url.scheme != "https" or repo_url.hostname != "github.com" or repo_url.port not in (None, 443) or repo_url.username or repo_url.password:
                    raise DatabaseValidationError(f"Dependency '{dep_id}' upstream_repository must be an HTTPS GitHub URL")
                repo_path = repo_url.path.strip("/")
                expected_prefix = f"/{repo_path}/releases/download/{item['release_tag']}/"
                if not parsed_url.path.startswith(expected_prefix):
                    raise DatabaseValidationError(f"Artifact '{var_key}' of '{dep_id}' is not under the cataloged repository/release tag")
                try:
                    size_bytes = int(art_data.get("size_bytes", 0))
                except (TypeError, ValueError) as exc:
                    raise DatabaseValidationError(f"Invalid size_bytes for artifact '{var_key}' of '{dep_id}'") from exc
                if size_bytes <= 0:
                    raise DatabaseValidationError(f"Artifact '{var_key}' of '{dep_id}' must declare a positive size_bytes")
                archive_type = str(art_data.get("archive_type", "zip")).lower()
                if archive_type not in {"zip"}:
                    raise DatabaseValidationError(f"Unsupported archive_type '{archive_type}' for '{dep_id}'")
                if not isinstance(art_data["asset_name"], str) or not re.fullmatch(r"[^\\/:*?\"<>|]+", art_data["asset_name"]):
                    raise DatabaseValidationError(f"Unsafe asset_name for artifact '{var_key}' of '{dep_id}'")
                variant_enum = ArtifactVariant(var_key.upper())

                artifacts[var_key.upper()] = DependencyArtifact(
                    asset_name=str(art_data["asset_name"]),
                    source_url=str(art_data["source_url"]),
                    sha256=sha_val,
                    size_bytes=size_bytes,
                    variant=variant_enum,
                    archive_type=archive_type,
                )

            spec = DependencySpec(
                id=dep_id,
                project_name=str(item["project_name"]),
                upstream_repository=str(item["upstream_repository"]),
                license=str(item["license"]),
                version=str(item["version"]),
                release_tag=str(item["release_tag"]),
                dependencies=[str(d).lower() for d in item.get("dependencies", [])],
                artifacts=artifacts,
                subcomponents=[str(s) for s in item.get("subcomponents", [])],
                date_verified=str(item.get("date_verified", "")),
            )
            dep_specs[dep_id] = spec

        for spec in dep_specs.values():
            missing = [parent for parent in spec.dependencies if parent not in dep_specs]
            if missing:
                raise DatabaseValidationError(
                    f"Dependency '{spec.id}' references missing parent(s): {', '.join(missing)}"
                )

        return cls(policy_version=policy_ver, dependencies=dep_specs)
