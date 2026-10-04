"""Small semantic contracts shared by guided clients."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import uuid


class ActionKind(str, Enum):
    AUTOMATIC = "automatic"
    HUMAN = "human"
    BLOCKED = "blocked"
    COMPLETE = "complete"


class Stage(str, Enum):
    DETECT = "detect"
    MATCH = "match"
    HARDWARE = "hardware"
    ACPI = "acpi"
    USB = "usb"
    TOOLS = "tools"
    DEPENDENCIES = "dependencies"
    IDENTITY = "identity"
    ACCEPTANCE = "acceptance"
    BUILD = "build"
    RECOVERY_MODE = "recovery-mode"
    RECOVERY = "recovery"
    MEDIA = "media"
    WRITE = "write"
    FIRST_BOOT = "first-boot"
    COMPLETE = "complete"


@dataclass(frozen=True)
class NextAction:
    stage: Stage
    kind: ActionKind
    message: str
    code: str = ""
    choices: tuple[str, ...] = ()
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(stage=self.stage.value, kind=self.kind.value, message=self.message,
                    code=self.code, choices=list(self.choices), destructive=self.destructive)


@dataclass
class AutoloaderSession:
    campaign_id: str
    campaign_digest: str
    machine_binding: str
    configuration_id: str
    snapshot_id: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    revision: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, dict[str, str]] = field(default_factory=dict)
    recovery_mode: str = "qualification"
    checkpoints: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(schema_version="1", campaign_id=self.campaign_id, campaign_digest=self.campaign_digest,
                    machine_binding=self.machine_binding, configuration_id=self.configuration_id,
                    snapshot_id=self.snapshot_id, session_id=self.session_id, revision=self.revision,
                    actions=self.actions, artifacts=self.artifacts, recovery_mode=self.recovery_mode,
                    checkpoints=self.checkpoints)

    @classmethod
    def from_dict(cls, data: Any) -> "AutoloaderSession":
        if not isinstance(data, dict) or data.get("schema_version") != "1":
            raise ValueError("Unsupported guided session")
        payload = dict(data)
        payload.pop("schema_version")
        session = cls(**payload)
        uuid.UUID(session.session_id)
        uuid.UUID(session.configuration_id)
        if session.revision < 0 or len(session.machine_binding) != 64:
            raise ValueError("Invalid guided session binding/revision")
        if not isinstance(session.actions, list) or not isinstance(session.artifacts, dict) or not isinstance(session.checkpoints, dict):
            raise ValueError("Invalid guided session records")
        if session.recovery_mode not in {"qualification", "smoke"}:
            raise ValueError("Invalid guided Recovery mode")
        return session
