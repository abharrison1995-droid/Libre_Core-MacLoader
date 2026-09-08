"""Safety-first removable media operations.

The platform adapters are intentionally injected. The shared contract refuses
ambiguous or system devices before an adapter can perform a destructive call.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from macloader.exceptions import MacLoaderError


class UnsafeRemovableTarget(MacLoaderError):
    """The selected device cannot be safely written."""


@dataclass(frozen=True)
class RemovableDevice:
    device_id: str
    model: str
    serial: Optional[str]
    capacity_bytes: int
    is_system_disk: bool
    is_removable: bool
    mounted: bool


@dataclass(frozen=True)
class WritePlan:
    target: RemovableDevice
    required_bytes: int
    partitions: List[str] = field(default_factory=lambda: ["GPT", "EFI", "Recovery"])
    destroys_data: bool = True


class RemovableMediaWriter:
    def __init__(self, destructive_write: Optional[Callable[[WritePlan, Path], None]] = None):
        self.destructive_write = destructive_write

    def dry_run(self, device: RemovableDevice, required_bytes: int) -> WritePlan:
        self._assert_safe(device, required_bytes)
        return WritePlan(device, required_bytes)

    def write(self, plan: WritePlan, source_dir: Path, confirmation: str) -> None:
        self._assert_safe(plan.target, plan.required_bytes)
        expected = f"WRITE {plan.target.device_id} {plan.target.capacity_bytes}"
        if confirmation != expected:
            raise UnsafeRemovableTarget("Confirmation does not match the exact device identity and capacity")
        if self.destructive_write is None:
            raise UnsafeRemovableTarget("No platform write adapter is configured")
        self.destructive_write(plan, source_dir)

    @staticmethod
    def _assert_safe(device: RemovableDevice, required_bytes: int) -> None:
        if not device.is_removable:
            raise UnsafeRemovableTarget("Target is not removable")
        if device.is_system_disk:
            raise UnsafeRemovableTarget("Refusing to modify the system disk")
        if device.mounted:
            raise UnsafeRemovableTarget("Target is mounted or busy")
        if device.capacity_bytes < required_bytes:
            raise UnsafeRemovableTarget("Target capacity is insufficient")
