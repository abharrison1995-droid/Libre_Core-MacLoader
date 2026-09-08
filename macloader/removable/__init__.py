"""Guarded removable-media planning and writing contracts."""

from macloader.removable.writer import (
    DisposableImageAdapter,
    RemovableDevice,
    RemovableMediaWriter,
    UnsafeRemovableTarget,
    WritePlan,
)

__all__ = [
    "DisposableImageAdapter",
    "RemovableDevice",
    "RemovableMediaWriter",
    "UnsafeRemovableTarget",
    "WritePlan",
]
