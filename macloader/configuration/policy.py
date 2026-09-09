"""Strict loaders for versioned configuration and exact-release policy data."""

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple
import yaml

from macloader.domain.contracts import canonical_json_digest
from macloader.domain.targets import MacOsTarget
from macloader.exceptions import DatabaseValidationError


DEFAULT_CONFIGURATION_DIR = Path(__file__).parent.parent / "database" / "data"


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DatabaseValidationError(f"Duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class MacOsRelease:
    product_id: str
    product_name: str
    version: str
    build: str
    release_record_digest: str
    qualification_state: str

    def target(self) -> MacOsTarget:
        return MacOsTarget(
            product_id=self.product_id, product_name=self.product_name,
            version=self.version, build=self.build,
            release_record_digest=self.release_record_digest,
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "product_id": self.product_id, "product_name": self.product_name,
            "version": self.version, "build": self.build,
            "release_record_digest": self.release_record_digest,
            "qualification_state": self.qualification_state,
        }


@dataclass(frozen=True)
class PolicyOption:
    option_id: str
    value_type: str
    control_hint: str
    allowed_values: Tuple[str, ...]
    default_value: Optional[str]
    applies_to_models: Tuple[str, ...]
    applies_to_products: Tuple[str, ...]
    dependencies: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    evidence_requirements: Tuple[str, ...]
    support_state: str
    requires_acknowledgement: bool
    explanation: str
    research_ref: str

    def validate_value(self, value: str) -> Optional[str]:
        if self.value_type != "enum":
            return f"Unsupported policy value type '{self.value_type}'"
        if value not in self.allowed_values:
            return f"Value '{value}' is not allowed for option '{self.option_id}'"
        return None


@dataclass(frozen=True)
class ConfigurationPolicy:
    schema_version: str
    policy_version: str
    model_id: str
    options: Mapping[str, PolicyOption]
    profiles: Tuple[str, ...]
    source_digest: str
    releases: Tuple[MacOsRelease, ...] = field(default_factory=tuple)

    def get_release(self, product_id: str, version: str, build: str) -> Optional[MacOsRelease]:
        return next(
            (item for item in self.releases if item.product_id == product_id and item.version == version and item.build == build),
            None,
        )

    def default_selections(self) -> Tuple[Tuple[str, str], ...]:
        return tuple(sorted((key, option.default_value) for key, option in self.options.items() if option.default_value is not None))


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.load(handle, Loader=_UniqueLoader)
    except DatabaseValidationError:
        raise
    except (OSError, yaml.YAMLError) as exc:
        raise DatabaseValidationError(f"Unable to load configuration policy {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DatabaseValidationError(f"Configuration policy {path} must be a mapping")
    return data


def _require_keys(data: Dict[str, Any], keys: set[str], label: str) -> None:
    missing = keys - set(data)
    unknown = set(data) - keys
    if missing or unknown:
        raise DatabaseValidationError(f"{label} has missing={sorted(missing)} unknown={sorted(unknown)}")


def _load_releases(data_dir: Path) -> Tuple[MacOsRelease, ...]:
    path = data_dir / "macos" / "releases.yaml"
    data = _read_yaml(path)
    _require_keys(data, {"schema_version", "policy_version", "releases"}, "macOS release catalog")
    if data["schema_version"] != "1" or not isinstance(data["releases"], list):
        raise DatabaseValidationError("macOS release catalog has an invalid schema")
    result: List[MacOsRelease] = []
    seen: set[Tuple[str, str, str]] = set()
    for raw in data["releases"]:
        if not isinstance(raw, dict):
            raise DatabaseValidationError("macOS release entries must be mappings")
        _require_keys(raw, {"product_id", "product_name", "version", "build", "qualification_state"}, "macOS release")
        key = (str(raw["product_id"]), str(raw["version"]), str(raw["build"]))
        if key in seen:
            raise DatabaseValidationError(f"Duplicate macOS release: {key}")
        seen.add(key)
        content = {"product_id": key[0], "product_name": str(raw["product_name"]), "version": key[1], "build": key[2], "qualification_state": str(raw["qualification_state"])}
        result.append(MacOsRelease(**content, release_record_digest=canonical_json_digest(content)))
    return tuple(result)


def _validate_profiles(data_dir: Path, profile_ids: List[Any], model_id: str) -> Tuple[str, ...]:
    profile_dir = data_dir / "profiles" / "t480s"
    validated: List[str] = []
    for raw_id in profile_ids:
        profile_id = str(raw_id)
        path = profile_dir / f"{profile_id.removeprefix('t480s-')}.yaml"
        if not path.is_file():
            # The catalog uses stable IDs while filenames may be descriptive.
            matches = list(profile_dir.glob("*.yaml"))
            path = next((candidate for candidate in matches if _read_yaml(candidate).get("profile_id") == profile_id), path)
        if not path.is_file():
            raise DatabaseValidationError(f"Configuration profile '{profile_id}' is missing")
        data = _read_yaml(path)
        _require_keys(data, {"schema_version", "profile_id", "model_id", "product_id", "capability_bindings"}, "configuration profile")
        if data["schema_version"] != "1" or data["profile_id"] != profile_id or data["model_id"] != model_id:
            raise DatabaseValidationError(f"Configuration profile '{profile_id}' does not match its catalog binding")
        bindings = data["capability_bindings"]
        if not isinstance(bindings, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in bindings.items()):
            raise DatabaseValidationError(f"Configuration profile '{profile_id}' capability_bindings must be a string mapping")
        validated.append(profile_id)
    return tuple(validated)


def load_configuration_policy(data_dir: Optional[Path] = None) -> ConfigurationPolicy:
    root = Path(data_dir) if data_dir else DEFAULT_CONFIGURATION_DIR
    path = root / "configuration" / "t480s.yaml"
    data = _read_yaml(path)
    _require_keys(data, {"schema_version", "policy_version", "model_id", "profiles", "options"}, "T480s configuration policy")
    if data["schema_version"] != "1" or not isinstance(data["profiles"], list) or not isinstance(data["options"], list):
        raise DatabaseValidationError("T480s configuration policy has an invalid schema")
    options: Dict[str, PolicyOption] = {}
    for raw in data["options"]:
        if not isinstance(raw, dict):
            raise DatabaseValidationError("configuration options must be mappings")
        expected = {"id", "value_type", "control_hint", "allowed_values", "default", "applies_to_models", "applies_to_products", "dependencies", "conflicts", "evidence_requirements", "support_state", "requires_acknowledgement", "explanation", "research_ref"}
        _require_keys(raw, expected, "configuration option")
        option_id = str(raw["id"])
        if option_id in options:
            raise DatabaseValidationError(f"Duplicate configuration option '{option_id}'")
        values = raw["allowed_values"]
        if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
            raise DatabaseValidationError(f"Option '{option_id}' allowed_values must be a non-empty string list")
        default = raw["default"]
        if default is not None and default not in values:
            raise DatabaseValidationError(f"Option '{option_id}' default is not an allowed value")
        options[option_id] = PolicyOption(
            option_id=option_id, value_type=str(raw["value_type"]), control_hint=str(raw["control_hint"]),
            allowed_values=tuple(values), default_value=default,
            applies_to_models=tuple(str(item) for item in raw["applies_to_models"]),
            applies_to_products=tuple(str(item) for item in raw["applies_to_products"]),
            dependencies=tuple(str(item) for item in raw["dependencies"]), conflicts=tuple(str(item) for item in raw["conflicts"]),
            evidence_requirements=tuple(str(item) for item in raw["evidence_requirements"]), support_state=str(raw["support_state"]),
            requires_acknowledgement=bool(raw["requires_acknowledgement"]), explanation=str(raw["explanation"]), research_ref=str(raw["research_ref"]),
        )
    for option in options.values():
        for ref in (*option.dependencies, *option.conflicts):
            if ref not in options:
                raise DatabaseValidationError(f"Option '{option.option_id}' references unknown option '{ref}'")
    releases = _load_releases(root)
    profiles = _validate_profiles(root, data["profiles"], str(data["model_id"]))
    digest_input = {"policy": data, "releases": [item.to_dict() for item in releases]}
    return ConfigurationPolicy(
        schema_version=str(data["schema_version"]), policy_version=str(data["policy_version"]), model_id=str(data["model_id"]),
        options=MappingProxyType(options), profiles=profiles,
        source_digest=canonical_json_digest(digest_input), releases=releases,
    )
