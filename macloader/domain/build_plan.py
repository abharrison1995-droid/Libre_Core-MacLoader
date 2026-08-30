"""Preliminary BuildPlan domain model."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
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
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
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
            "timestamp": self.timestamp,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BuildPlan":
        return cls(
            plan_id=data.get("plan_id", str(uuid.uuid4())),
            target_model=data["target_model"],
            target_macos=data["target_macos"],
            hardware_snapshot_id=data["hardware_snapshot_id"],
            support_state=CompatibilityState(data["support_state"]),
            required_capabilities=list(data.get("required_capabilities", [])),
            planned_components=[dict(c) for c in data.get("planned_components", [])],
            unresolved_requirements=list(data.get("unresolved_requirements", [])),
            warnings=list(data.get("warnings", [])),
            is_preliminary=bool(data.get("is_preliminary", True)),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )
