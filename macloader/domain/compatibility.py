"""Compatibility domain models for MacLoader."""

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Dict, List, Optional


class CompatibilityState(str, Enum):
    """Strict compatibility state enum."""
    SUPPORTED = "SUPPORTED"
    CONDITIONAL = "CONDITIONAL"
    EXPERIMENTAL = "EXPERIMENTAL"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_usable(self) -> bool:
        """Return True if the state does not outright block installation."""
        return self in (
            CompatibilityState.SUPPORTED,
            CompatibilityState.CONDITIONAL,
            CompatibilityState.EXPERIMENTAL,
        )


@dataclass
class SupportDecision:
    """Represents a support decision for a model, component, or configuration."""
    target: str
    state: CompatibilityState
    reason: str
    evidence: str = ""
    required_actions: List[str] = field(default_factory=list)
    known_limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "state": self.state.value,
            "reason": self.reason,
            "evidence": self.evidence,
            "required_actions": list(self.required_actions),
            "known_limitations": list(self.known_limitations),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SupportDecision":
        return cls(
            target=data["target"],
            state=CompatibilityState(data["state"]),
            reason=data["reason"],
            evidence=data.get("evidence", ""),
            required_actions=list(data.get("required_actions", [])),
            known_limitations=list(data.get("known_limitations", [])),
        )


@dataclass
class ComponentCompatibilityResult:
    """Compatibility evaluation result for an individual hardware component."""
    category: str
    component_id: str
    component_name: str
    decision: SupportDecision

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "component_id": self.component_id,
            "component_name": self.component_name,
            "decision": self.decision.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComponentCompatibilityResult":
        return cls(
            category=data["category"],
            component_id=data["component_id"],
            component_name=data["component_name"],
            decision=SupportDecision.from_dict(data["decision"]),
        )


@dataclass
class CompatibilityReport:
    """Full compatibility assessment report for a detected snapshot and target macOS."""
    snapshot_id: str
    target_macos: str
    model_id: Optional[str]
    model_name: str
    overall_state: CompatibilityState
    model_decision: SupportDecision
    component_results: List[ComponentCompatibilityResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unresolved_requirements: List[str] = field(default_factory=list)
    can_generate_build_plan: bool = False
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "target_macos": self.target_macos,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "overall_state": self.overall_state.value,
            "model_decision": self.model_decision.to_dict(),
            "component_results": [r.to_dict() for r in self.component_results],
            "warnings": list(self.warnings),
            "unresolved_requirements": list(self.unresolved_requirements),
            "can_generate_build_plan": self.can_generate_build_plan,
            "timestamp": self.timestamp,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompatibilityReport":
        return cls(
            snapshot_id=data["snapshot_id"],
            target_macos=data["target_macos"],
            model_id=data.get("model_id"),
            model_name=data.get("model_name", "Unknown"),
            overall_state=CompatibilityState(data["overall_state"]),
            model_decision=SupportDecision.from_dict(data["model_decision"]),
            component_results=[
                ComponentCompatibilityResult.from_dict(r)
                for r in data.get("component_results", [])
            ],
            warnings=list(data.get("warnings", [])),
            unresolved_requirements=list(data.get("unresolved_requirements", [])),
            can_generate_build_plan=bool(data.get("can_generate_build_plan", False)),
            timestamp=data.get("timestamp", ""),
        )
