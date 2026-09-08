"""Preliminary BuildPlan domain model."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import hashlib
from typing import Any, Dict, List, Optional
import uuid

from macloader.domain.compatibility import CompatibilityState


@dataclass
class BuildPlan:
    """Represents a preliminary OpenCore EFI build plan describing required capabilities and future resolutions."""
    target_model: str
    target_macos: str
    hardware_snapshot_id: str
    support_state: CompatibilityState
    required_capabilities: List[str] = field(default_factory=list)
    planned_components: List[Dict[str, str]] = field(default_factory=list)
    unresolved_requirements: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_preliminary: bool = True
    is_actionable: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = "0.1"
    policy_version: str = ""
    build_ready: bool = False

    def to_dict(self) -> Dict[str, Any]:
        derived_actionable = self.support_state.is_usable
        derived_build_ready = derived_actionable and not self.unresolved_requirements
        return {
            "plan_id": self.plan_id,
            "target_model": self.target_model,
            "target_macos": self.target_macos,
            "hardware_snapshot_id": self.hardware_snapshot_id,
            "support_state": self.support_state.value,
            "required_capabilities": list(self.required_capabilities),
            "planned_components": [dict(c) for c in self.planned_components],
            "unresolved_requirements": list(self.unresolved_requirements),
            "warnings": list(self.warnings),
            "is_preliminary": self.is_preliminary,
            "is_actionable": derived_actionable,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "build_ready": derived_build_ready,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def canonical_digest(self) -> str:
        """Return a stable identity for equivalent plans, excluding audit fields."""
        data = self.to_dict()
        data.pop("plan_id", None)
        data.pop("timestamp", None)
        # These are derived from support_state and unresolved_requirements.
        data.pop("is_actionable", None)
        data.pop("build_ready", None)
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BuildPlan":
        support_state = CompatibilityState(data["support_state"])
        raw_unresolved = data.get("unresolved_requirements", [])
        if not isinstance(raw_unresolved, list) or not all(isinstance(item, str) for item in raw_unresolved):
            raise ValueError("BuildPlan unresolved_requirements must be a list of strings")
        unresolved_requirements = list(raw_unresolved)
        derived_actionable = support_state.is_usable
        derived_build_ready = derived_actionable and not unresolved_requirements
        serialized_actionable = data.get("is_actionable")
        serialized_ready = data.get("build_ready")
        if serialized_actionable is not None and not isinstance(serialized_actionable, bool):
            raise ValueError("BuildPlan is_actionable must be a boolean")
        if serialized_ready is not None and not isinstance(serialized_ready, bool):
            raise ValueError("BuildPlan build_ready must be a boolean")
        if serialized_actionable is not None and serialized_actionable != derived_actionable:
            raise ValueError("BuildPlan is_actionable does not match its support_state")
        if serialized_ready is not None and serialized_ready != derived_build_ready:
            raise ValueError("BuildPlan build_ready does not match its validated requirements")
        return cls(
            plan_id=data.get("plan_id", str(uuid.uuid4())),
            target_model=data["target_model"],
            target_macos=data["target_macos"],
            hardware_snapshot_id=data["hardware_snapshot_id"],
            support_state=support_state,
            required_capabilities=list(data.get("required_capabilities", [])),
            planned_components=[dict(c) for c in data.get("planned_components", [])],
            unresolved_requirements=unresolved_requirements,
            warnings=list(data.get("warnings", [])),
            is_preliminary=bool(data.get("is_preliminary", True)),
            is_actionable=derived_actionable,
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            schema_version=data.get("schema_version", "0.1"),
            policy_version=data.get("policy_version", ""),
            build_ready=derived_build_ready,
        )
