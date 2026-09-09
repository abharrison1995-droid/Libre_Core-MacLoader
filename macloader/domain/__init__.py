"""Domain models for MacLoader."""

from macloader.domain.compatibility import (
    CompatibilityState,
    SupportDecision,
    ComponentCompatibilityResult,
    CompatibilityReport,
)
from macloader.domain.hardware import (
    PciDevice,
    UsbDevice,
    CpuInfo,
    GpuInfo,
    AudioInfo,
    NetworkInfo,
    StorageInfo,
    InputDeviceInfo,
    DisplayInfo,
    ThunderboltInfo,
    HardwareSnapshot,
    normalize_inventory_status,
    normalize_raw_evidence,
)
from macloader.domain.build_plan import BuildPlan
from macloader.domain.dependencies import (
    ArtifactVariant,
    DependencyArtifact,
    DependencySpec,
    ResolvedDependency,
    ResolvedDependencySet,
)
from macloader.domain.targets import MacOsTarget
from macloader.domain.evidence import EvidenceCompleteness, EvidenceConfidence, EvidenceRecord
from macloader.domain.configuration import (
    AcceptedConfiguration,
    Acknowledgement,
    BlockingStage,
    ConfigurationIssue,
    HardwareConfirmation,
    HardwareObservation,
    IssueSeverity,
    ObservationStatus,
    UserConfiguration,
)

__all__ = [
    "CompatibilityState",
    "SupportDecision",
    "ComponentCompatibilityResult",
    "CompatibilityReport",
    "PciDevice",
    "UsbDevice",
    "CpuInfo",
    "GpuInfo",
    "AudioInfo",
    "NetworkInfo",
    "StorageInfo",
    "InputDeviceInfo",
    "DisplayInfo",
    "ThunderboltInfo",
    "HardwareSnapshot",
    "normalize_inventory_status",
    "normalize_raw_evidence",
    "BuildPlan",
    "ArtifactVariant",
    "DependencyArtifact",
    "DependencySpec",
    "ResolvedDependency",
    "ResolvedDependencySet",
    "MacOsTarget",
    "EvidenceCompleteness",
    "EvidenceConfidence",
    "EvidenceRecord",
    "AcceptedConfiguration",
    "Acknowledgement",
    "BlockingStage",
    "ConfigurationIssue",
    "HardwareConfirmation",
    "HardwareObservation",
    "IssueSeverity",
    "ObservationStatus",
    "UserConfiguration",
]
