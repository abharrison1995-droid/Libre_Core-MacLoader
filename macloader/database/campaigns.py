"""Validated compositions of existing reviewed policy records.

A campaign names the reference machine and links lower-level policy. It does
not own graphics/audio tuning, port routes, dependencies or tool versions.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

from macloader.build.config import ReviewedEfiProfile, load_reviewed_profile
from macloader.configuration.policy import (
    ConfigurationPolicy, MacOsRelease, _read_yaml, _require_keys, load_configuration_policy,
)
from macloader.domain.contracts import canonical_json_digest
from macloader.exceptions import DatabaseValidationError

if TYPE_CHECKING:
    from macloader.database.loader import Database


@dataclass(frozen=True)
class ReferenceCampaign:
    campaign_id: str
    revision: str
    model_id: str
    machine_type: str
    bios_binding: str
    release: MacOsRelease
    profile: ReviewedEfiProfile
    configuration_policy: ConfigurationPolicy
    evidence_policy_id: str
    evidence_policy_digest: str
    evidence_policy: Mapping[str, Any]
    recovery_policy_id: str
    required_components: Mapping[str, tuple[str, ...]]
    required_evidence: tuple[str, ...]
    support_state: str
    first_boot_instruction: str
    smoke_recovery_eligible: bool
    smoke_recovery_policy: Mapping[str, Any]
    digest: str


def _unique_record(root: Path, field: str, identity: str) -> tuple[Path, dict[str, Any]]:
    matches = [(path, _read_yaml(path)) for path in sorted(root.glob("**/*.yaml"))]
    matches = [(path, data) for path, data in matches if data.get(field) == identity]
    if len(matches) != 1:
        raise DatabaseValidationError(f"Campaign reference {field} is missing or ambiguous")
    return matches[0]


def load_campaigns(db: "Database") -> dict[str, ReferenceCampaign]:
    result: dict[str, ReferenceCampaign] = {}
    selectors: set[tuple[str, str, str]] = set()
    for path in sorted((db.data_dir / "campaigns").glob("*.yaml")):
        raw = _read_yaml(path)
        _require_keys(raw, {
            "schema_version", "campaign_id", "revision", "model_id", "machine_type", "bios_binding",
            "target", "reviewed_profile_id", "configuration_policy_id", "evidence_policy_id",
            "recovery_policy_id", "required_components", "required_evidence", "support_state",
            "first_boot_instruction", "smoke_recovery_eligible", "smoke_recovery_policy_id",
        }, "reference campaign")
        if raw["schema_version"] != "1" or raw["support_state"] != "EXPERIMENTAL":
            raise DatabaseValidationError("Reference campaign schema/support state is not reviewed")
        if type(raw["smoke_recovery_eligible"]) is not bool:
            raise DatabaseValidationError("Campaign smoke eligibility must be boolean")
        identity = str(raw["campaign_id"])
        model_id, machine_type, bios = (str(raw[key]) for key in ("model_id", "machine_type", "bios_binding"))
        if not identity or not str(raw["revision"]) or identity in result:
            raise DatabaseValidationError("Duplicate or empty campaign identity")
        selector = (model_id, machine_type, bios)
        if selector in selectors:
            raise DatabaseValidationError("Ambiguous reference campaign eligibility")
        selectors.add(selector)
        model = db.get_model(model_id)
        if model is None or machine_type not in model.machine_types:
            raise DatabaseValidationError("Campaign machine/model reference does not agree")
        policy = load_configuration_policy(db.data_dir, policy_id=str(raw["configuration_policy_id"]))
        target = raw["target"]
        if not isinstance(target, dict):
            raise DatabaseValidationError("Campaign target must reference an exact release")
        _require_keys(target, {"product_id", "version", "build"}, "campaign target")
        release = policy.get_release(*(str(target[key]) for key in ("product_id", "version", "build")))
        if release is None:
            raise DatabaseValidationError("Campaign release reference is missing")
        profile_path, _ = _unique_record(db.data_dir / "profiles", "profile_id", str(raw["reviewed_profile_id"]))
        profile = load_reviewed_profile(profile_path)
        if (profile.model_id, profile.bios_binding, profile.product_id, profile.version, profile.build) != (
            model_id, bios, release.product_id, release.version, release.build,
        ) or policy.model_id != model_id:
            raise DatabaseValidationError("Campaign profile/policy/release/BIOS references disagree")
        if policy.options["profile.audio"].default_value != f"layout-{profile.audio.layout_id}":
            raise DatabaseValidationError("Campaign audio default disagrees with reviewed profile")
        _, evidence = _unique_record(db.data_dir / "evidence", "policy_version", str(raw["evidence_policy_id"]))
        if evidence.get("model_id") != model_id or evidence.get("schema_version") != "1":
            raise DatabaseValidationError("Campaign evidence policy reference disagrees")
        capture = evidence.get("usb_capture")
        if not isinstance(capture, dict) or set(capture) != {"minimum_routes", "excluded_routes", "internal_labels", "steps"}:
            raise DatabaseValidationError("Campaign USB capture policy is missing")
        if not isinstance(capture["steps"], list) or not capture["steps"]:
            raise DatabaseValidationError("Campaign USB physical steps are missing")
        if profile.usb.first_install_route not in capture["minimum_routes"] or set(capture["minimum_routes"]) & set(capture["excluded_routes"]):
            raise DatabaseValidationError("Campaign USB route scope conflicts")
        for step in capture["steps"]:
            if not isinstance(step, dict) or not {"label", "connector", "speed"} <= set(step) or set(step) - {"label", "connector", "speed", "orientation"}:
                raise DatabaseValidationError("Campaign USB step schema is invalid")
            if not isinstance(step["label"], str) or not step["label"] or step["connector"] not in {"USB-A", "USB-C"} or step["speed"] not in {"high", "super"}:
                raise DatabaseValidationError("Campaign USB step values are invalid")
            if step["connector"] == "USB-C" and step.get("orientation") not in {"normal", "flipped"}:
                raise DatabaseValidationError("Campaign USB-C orientation is required")
        _, recovery = _unique_record(db.data_dir / "recovery", "policy_id", str(raw["recovery_policy_id"]))
        targets = recovery.get("targets")
        if not isinstance(targets, list) or not any(
            all(str(item.get(key)) == str(target[key]) for key in target)
            for item in targets if isinstance(item, dict)
        ):
            raise DatabaseValidationError("Campaign Recovery target reference disagrees")
        _, smoke = _unique_record(db.data_dir / "recovery", "policy_id", str(raw["smoke_recovery_policy_id"]))
        if smoke.get("purpose") != "picker-recovery-smoke-only" or smoke.get("metadata_trust") != "untrusted" or smoke.get("query_scheme") != "http" or smoke.get("asset_scheme") != "https" or smoke.get("actual_build") != "unknown" or smoke.get("installation_authorized") is not False:
            raise DatabaseValidationError("Smoke Recovery policy trust boundaries are invalid")
        components = raw["required_components"]
        if not isinstance(components, dict) or not components:
            raise DatabaseValidationError("Campaign component references are missing")
        normalized: dict[str, tuple[str, ...]] = {}
        for category, ids in components.items():
            if not isinstance(category, str) or not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                raise DatabaseValidationError("Campaign component references are invalid")
            for component_id in ids:
                component = db.get_component(component_id)
                if component is None or component.category != category:
                    raise DatabaseValidationError("Campaign component reference is missing or wrong category")
            normalized[category] = tuple(ids)
        required = raw["required_evidence"]
        if not isinstance(required, list) or set(required) != {"acpi", "usb"}:
            raise DatabaseValidationError("Campaign requires machine ACPI and physical USB evidence")
        digest = canonical_json_digest({
            "campaign": raw, "configuration": policy.source_digest, "profile": profile.source_digest,
            "release": release.release_record_digest, "evidence": evidence, "recovery": recovery, "smoke_recovery": smoke,
            "model": asdict(model),
            "components": {key: asdict(db.components[key]) for ids in normalized.values() for key in ids},
            "capability_profiles": [
                _unique_record(db.data_dir / "profiles", "profile_id", key)[1]
                for key in policy.profiles
            ],
        })
        result[identity] = ReferenceCampaign(
            identity, str(raw["revision"]), model_id, machine_type, bios, release, profile, policy,
            str(raw["evidence_policy_id"]), canonical_json_digest(evidence), MappingProxyType(evidence), str(raw["recovery_policy_id"]),
            MappingProxyType(normalized), tuple(required), str(raw["support_state"]),
            str(raw["first_boot_instruction"]), raw["smoke_recovery_eligible"], MappingProxyType(smoke), digest,
        )
    return result
