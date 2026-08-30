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
)
from macloader.domain.build_plan import BuildPlan
from macloader.domain.dependencies import (
    ArtifactVariant,
    DependencyArtifact,
    DependencySpec,
    ResolvedDependency,
    ResolvedDependencySet,
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
    "BuildPlan",
    "ArtifactVariant",
    "DependencyArtifact",
    "DependencySpec",
    "ResolvedDependency",
    "ResolvedDependencySet",
]
