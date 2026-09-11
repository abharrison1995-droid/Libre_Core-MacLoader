"""Guarded removable-media planning and writing contracts."""

from macloader.removable.writer import (
    DestructiveConfirmation,
    DisposableImageAdapter,
    MediaBindings,
    MediaFileDigest,
    RemovableDevice,
    RemovableMediaWriter,
    UnsafeRemovableTarget,
    WritePlan,
)
from macloader.removable.adapters import (
    AdapterStatus,
    LinuxRemovableAdapter,
    WindowsRemovableAdapter,
    current_adapter,
)

__all__ = [
    "AdapterStatus",
    "DestructiveConfirmation",
    "DisposableImageAdapter",
    "LinuxRemovableAdapter",
    "MediaBindings",
    "MediaFileDigest",
    "RemovableDevice",
    "RemovableMediaWriter",
    "UnsafeRemovableTarget",
    "WindowsRemovableAdapter",
    "WritePlan",
    "current_adapter",
]
