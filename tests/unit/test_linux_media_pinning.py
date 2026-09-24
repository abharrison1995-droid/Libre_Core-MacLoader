"""Deterministic identity-pinning, race and corruption tests for the Linux writer.

A fake kernel models the locked descriptor, attachment-unique paths, by-id
aliases, partitions and mounts.  Commands are emulated in pure Python against
a disposable image file, so these tests never open a host block device.
"""

from dataclasses import replace
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
from typing import Callable, Optional
import uuid
import zlib

import pytest

import macloader.removable.adapters as adapters_module
from macloader.removable import MediaBindings, RemovableDevice, UnsafeRemovableTarget
from macloader.removable.adapters import (
    ESP_FIRST_LBA,
    ESP_TYPE_GUID,
    GPT_ENTRY_SECTORS,
    BlockIdentity,
    LinuxBlockDeviceBackend,
    LinuxBlockProbe,
    PartitionGeometry,
    verify_fat32_volume,
    verify_gpt_layout,
)
from macloader.removable.writer import FAT32_MAX_FILE_BYTES, MediaFileDigest, RemovableMediaWriter, WritePlan


QUALIFIED = MediaBindings("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64, "f" * 64)
CAPACITY = 80 * 1024 * 1024 + 512
SECTORS = CAPACITY // 512
PARTITION_SECTORS = SECTORS - ESP_FIRST_LBA - 1 - GPT_ENTRY_SECTORS
DISK_RDEV = os.makedev(8, 16)
PART_RDEV = os.makedev(8, 17)
OTHER_RDEV = os.makedev(8, 32)
ALIAS = "usb-MAKER_PIN001-0:0"


# --- synthetic on-media structures -------------------------------------------------


def _gpt_header(my_lba: int, alternate: int, entries_lba: int, entries_crc: int, disk_guid: bytes) -> bytes:
    last_usable = SECTORS - 2 - GPT_ENTRY_SECTORS
    header = struct.pack(
        "<8sIIIIQQQQ16sQIII", b"EFI PART", 0x00010000, 92, 0, 0, my_lba, alternate,
        ESP_FIRST_LBA, last_usable, disk_guid, entries_lba, 128, 128, entries_crc,
    )
    crc = zlib.crc32(header) & 0xFFFFFFFF
    header = header[:16] + struct.pack("<I", crc) + header[20:]
    return header + bytes(512 - len(header))


def _gpt_entries(type_guid: uuid.UUID = ESP_TYPE_GUID, first: int = ESP_FIRST_LBA, count: int = PARTITION_SECTORS) -> bytes:
    name = "MACLOADER".encode("utf-16-le").ljust(72, b"\x00")
    entry = struct.pack("<16s16sQQQ72s", type_guid.bytes_le, uuid.UUID(int=7).bytes_le, first, first + count - 1, 0, name)
    return entry + bytes(128 * 128 - len(entry))


def write_gpt(image: Path, entries: Optional[bytes] = None) -> None:
    entries = entries if entries is not None else _gpt_entries()
    crc = zlib.crc32(entries) & 0xFFFFFFFF
    disk_guid = uuid.UUID(int=99).bytes_le
    last = SECTORS - 1
    mbr = bytearray(512)
    mbr[446 + 4] = 0xEE
    mbr[510:512] = b"\x55\xaa"
    with image.open("r+b") as handle:
        handle.seek(0)
        handle.write(bytes(mbr))
        handle.write(_gpt_header(1, last, 2, crc, disk_guid))
        handle.write(entries)
        handle.seek((last - GPT_ENTRY_SECTORS) * 512)
        handle.write(entries)
        handle.write(_gpt_header(last, 1, last - GPT_ENTRY_SECTORS, crc, disk_guid))


def write_fat32(image: Path, start: int = ESP_FIRST_LBA, size: int = PARTITION_SECTORS) -> None:
    boot = bytearray(512)
    boot[0:3] = b"\xebX\x90"
    boot[3:11] = b"mkfs.fat"
    reserved, fat_size = 32, 1024
    struct.pack_into(
        "<HBHBHHBHHHIIIHHIHH", boot, 11, 512, 1, reserved, 2, 0, 0, 0xF8, 0, 32, 64, start, size,
        fat_size, 0, 0, 2, 1, 6,
    )
    boot[0x42] = 0x29
    boot[0x47:0x52] = b"MACLOADER  "
    boot[0x52:0x5A] = b"FAT32   "
    boot[510:512] = b"\x55\xaa"
    fsinfo = bytearray(512)
    fsinfo[0:4] = b"RRaA"
    fsinfo[484:488] = b"rrAa"
    fsinfo[508:512] = b"\x00\x00\x55\xaa"
    fat_head = struct.pack("<III", 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFFF)
    base = start * 512
    with image.open("r+b") as handle:
        for sector, data in ((0, boot), (1, fsinfo), (6, boot), (7, fsinfo)):
            handle.seek(base + sector * 512)
            handle.write(bytes(data))
        for index in range(2):
            handle.seek(base + (reserved + index * fat_size) * 512)
            handle.write(fat_head)


def _patch(image: Path, offset: int, data: bytes) -> None:
    with image.open("r+b") as handle:
        handle.seek(offset)
        handle.write(data)


# --- fake kernel -------------------------------------------------------------------


class FakeKernel(LinuxBlockProbe):
    """In-memory model of the locked disk, its private nodes and partition."""

    def __init__(self, tmp_path: Path, image: Path, by_id_root: Path) -> None:
        self.node_parent = tmp_path / "devnodes"
        self.node_parent.mkdir()
        self.image = image
        self.disk = BlockIdentity(DISK_RDEV, 7, CAPACITY, 512)
        self.held = self.disk
        self.alias = by_id_root / ALIAS
        self.aliases: dict[str, BlockIdentity] = {str(self.alias): self.disk}
        # What a node for a given dev_t currently opens (the kernel's view).
        self.by_rdev: dict[int, BlockIdentity] = {DISK_RDEV: self.disk}
        self.nodes: dict[str, int] = {}
        self.fd_kind: dict[int, str] = {}
        self.part_held: Optional[BlockIdentity] = None
        self.geometry: dict[int, PartitionGeometry] = {}
        self.mounts: dict[str, int] = {}
        self.cache_drops = 0
        self.rereads = 0
        self.lock_error: Optional[OSError] = None
        self.partition_args: Optional[dict[str, int]] = {}

    def open_locked(self, path: Path) -> int:
        if self.lock_error is not None:
            raise self.lock_error
        if str(path) not in self.aliases:
            raise FileNotFoundError(path)
        descriptor = os.open(self.image, os.O_RDWR)
        self.fd_kind[descriptor] = "disk"
        return descriptor

    def open_readonly(self, path: Path) -> int:
        identity = self.identity_of_path(path)
        descriptor = os.open(self.image, os.O_RDONLY)
        self.fd_kind[descriptor] = "disk" if identity.rdev == DISK_RDEV else "part"
        return descriptor

    def identity_of_fd(self, descriptor: int) -> BlockIdentity:
        if self.fd_kind.get(descriptor) == "part":
            assert self.part_held is not None
            return self.part_held
        return self.held

    def identity_of_path(self, path: Path) -> BlockIdentity:
        if str(path) in self.aliases:
            return self.aliases[str(path)]
        if str(path) in self.nodes and Path(path).exists():
            identity = self.by_rdev.get(self.nodes[str(path)])
            if identity is not None:
                return identity
        raise FileNotFoundError(path)

    def make_node(self, directory: Path, name: str, rdev: int) -> Path:
        path = directory / name
        path.write_bytes(b"")
        self.nodes[str(path)] = rdev
        return path

    def reread_partitions(self, descriptor: int) -> None:
        self.rereads += 1
        if self.partition_args is not None:
            self.create_partition(**self.partition_args)

    def find_partition(self, disk_rdev: int, number: int) -> Optional[int]:
        return PART_RDEV if PART_RDEV in self.geometry and number == 1 else None

    def partition_geometry(self, rdev: int) -> PartitionGeometry:
        return self.geometry[rdev]

    def is_partition(self, rdev: int) -> bool:
        return rdev in self.geometry

    def drop_cache(self, descriptor: int) -> None:
        self.cache_drops += 1

    def mounted_device(self, mount: Path) -> int:
        return self.mounts[str(mount)]

    def is_mount(self, mount: Path) -> bool:
        return str(mount) in self.mounts

    def create_partition(
        self, start: int = ESP_FIRST_LBA, size: int = PARTITION_SECTORS, parent: int = DISK_RDEV,
        diskseq: Optional[int] = None,
    ) -> None:
        self.geometry[PART_RDEV] = PartitionGeometry(parent, 1, start, size)
        self.part_held = BlockIdentity(PART_RDEV, diskseq or self.disk.diskseq, size * 512, 512)
        self.by_rdev[PART_RDEV] = self.part_held

    def hotplug_replace(self) -> None:
        """Model unplug + replug: same alias and dev_t, new attachment sequence."""
        replacement = replace(self.disk, diskseq=self.disk.diskseq + 1)
        self.held = replacement
        self.aliases = {str(self.alias): replacement}
        self.by_rdev[DISK_RDEV] = replacement


class Harness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path) -> None:
        self.tmp_path = tmp_path
        self.source = source
        self.image = tmp_path / "disposable.img"
        with self.image.open("wb") as handle:
            handle.truncate(CAPACITY)
        self.by_id = tmp_path / "by-id"
        self.by_id.mkdir()
        self.kernel = FakeKernel(tmp_path, self.image, self.by_id)
        self.volume = tmp_path / "volume"
        self.volume.mkdir()
        self.mount_parent = tmp_path / "mounts"
        self.mount_parent.mkdir()
        self.commands: list[list[str]] = []
        self.hooks: dict[str, Callable[[list[str]], None]] = {}
        self.fail: set[str] = set()
        self.device = RemovableDevice(f"linux:by-id:{ALIAS}", "USB", CAPACITY, False, True, False, serial="PIN001")
        self.backend = LinuxBlockDeviceBackend(lambda: [self.device], runner=self.run, probe=self.kernel)
        self.backend._mount_parent = self.mount_parent
        monkeypatch.setattr(LinuxBlockDeviceBackend, "_by_id_root", self.by_id)
        monkeypatch.setattr(LinuxBlockDeviceBackend, "_partition_wait_seconds", 0.0)
        monkeypatch.setattr(adapters_module.os, "geteuid", lambda: 0, raising=False)
        monkeypatch.setattr(self.backend, "_current", lambda expected, _required=1, **_kwargs: self.device)
        self.plan = RemovableMediaWriter().dry_run(self.device, 1, source_dir=source, bindings=QUALIFIED)

    def run(self, args: list[str], _input: Optional[str] = None) -> str:
        self.commands.append(list(args))
        hook = self.hooks.get(args[0])
        if hook is not None:
            hook(args)
        if " ".join(args[:2]) in self.fail or args[0] in self.fail:
            raise UnsafeRemovableTarget(f"Linux media operation failed ({args[0]})")
        if args[0] == "sfdisk":
            write_gpt(self.image)
        elif args[0] == "mkfs.vfat":
            write_fat32(self.image)
            shutil.rmtree(self.volume)
            self.volume.mkdir()
        elif args[0] == "mount":
            mount = Path(args[-1])
            shutil.copytree(self.volume, mount, dirs_exist_ok=True)
            self.kernel.mounts[str(mount)] = self.kernel.identity_of_path(Path(args[-2])).rdev
        elif args[0] == "umount":
            mount = Path(args[-1])
            shutil.rmtree(self.volume)
            shutil.copytree(mount, self.volume)
            for child in mount.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            self.kernel.mounts.pop(str(mount), None)
        return ""

    def names(self) -> list[str]:
        return [command[0] for command in self.commands]

    def device_args(self) -> list[str]:
        return [
            arg for command in self.commands for arg in command
            if arg.startswith(str(self.tmp_path)) and not arg.startswith(str(self.mount_parent))
        ]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "EFI" / "BOOT").mkdir(parents=True)
    (root / "EFI" / "OC").mkdir(parents=True)
    (root / "Recovery").mkdir()
    (root / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(b"boot")
    (root / "EFI" / "OC" / "OpenCore.efi").write_bytes(b"oc")
    (root / "Recovery" / "BaseSystem.dmg").write_bytes(b"recovery")
    return root


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path) -> Harness:
    return Harness(tmp_path, monkeypatch, source)


# --- pinned lifecycle --------------------------------------------------------------


def test_every_media_command_uses_private_nodes_of_the_locked_disk(harness: Harness) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    node_dir = backend._locked[harness.device.device_id].node_dir
    assert stat.S_IMODE(node_dir.stat().st_mode) == 0o700
    backend.write(harness.plan, harness.source)
    backend.flush(harness.device)
    assert backend.readback(harness.plan, harness.source) is True
    backend.safe_eject(harness.device)

    assert harness.names() == [
        "sfdisk", "mkfs.vfat", "mount", "umount", "blockdev", "mount", "umount", "blockdev", "udisksctl",
    ]
    assert harness.kernel.rereads == 1, "partition table re-read goes through the locked descriptor"
    device_args = harness.device_args()
    assert device_args and all(arg.startswith(str(node_dir)) for arg in device_args)
    assert not any(ALIAS in arg or "by-diskseq" in arg for command in harness.commands for arg in command)
    assert "--no-reread" in harness.commands[0]
    assert harness.kernel.cache_drops == 2
    assert backend._locked == {} and backend._mounts == {}
    assert not node_dir.exists(), "private nodes are removed with the lock"
    assert (harness.volume / "com.apple.recovery.boot" / "BaseSystem.dmg").read_bytes() == b"recovery"


def test_lock_rejects_identity_that_is_not_the_planned_whole_disk(harness: Harness) -> None:
    kernel = harness.kernel
    kernel.held = replace(kernel.disk, size_bytes=CAPACITY - 512)
    with pytest.raises(UnsafeRemovableTarget, match="not the planned whole 512-byte-sector disk"):
        harness.backend.lock_and_dismount(harness.device)
    kernel.held = replace(kernel.disk, logical_sector_bytes=4096)
    with pytest.raises(UnsafeRemovableTarget, match="512-byte-sector"):
        harness.backend.lock_and_dismount(harness.device)
    kernel.held = kernel.disk
    kernel.geometry[DISK_RDEV] = PartitionGeometry(OTHER_RDEV, 1, 2048, 1)
    with pytest.raises(UnsafeRemovableTarget, match="whole 512-byte-sector disk"):
        harness.backend.lock_and_dismount(harness.device)
    kernel.geometry.clear()
    kernel.lock_error = BlockingIOError("busy")
    with pytest.raises(UnsafeRemovableTarget, match="Unable to exclusively lock"):
        harness.backend.lock_and_dismount(harness.device)
    assert harness.backend._locked == {} and harness.commands == []
    assert list(kernel.node_parent.iterdir()) == []


def test_lock_rejects_alias_retargeted_between_open_and_verification(harness: Harness) -> None:
    kernel = harness.kernel
    kernel.aliases[str(kernel.alias)] = BlockIdentity(OTHER_RDEV, 3, CAPACITY, 512)
    with pytest.raises(UnsafeRemovableTarget, match="by-id alias now refers to a different device"):
        harness.backend.lock_and_dismount(harness.device)
    assert harness.backend._locked == {}
    assert list(kernel.node_parent.iterdir()) == []


def test_double_lock_and_unlocked_operations_are_refused(harness: Harness) -> None:
    backend = harness.backend
    with pytest.raises(UnsafeRemovableTarget, match="lock was lost before write"):
        backend.write(harness.plan, harness.source)
    with pytest.raises(UnsafeRemovableTarget, match="lock was lost before flush"):
        backend.flush(harness.device)
    with pytest.raises(UnsafeRemovableTarget, match="lock was lost before readback"):
        backend.readback(harness.plan, harness.source)
    backend.invalidate(harness.device, "not locked")
    backend.safe_eject(harness.device)
    backend.lock_and_dismount(harness.device)
    with pytest.raises(UnsafeRemovableTarget, match="already locked"):
        backend.lock_and_dismount(harness.device)
    backend._close_lock(harness.device.device_id)
    assert harness.commands == []


def test_alias_retarget_after_lock_stops_before_repartitioning(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.kernel.aliases[str(harness.kernel.alias)] = BlockIdentity(OTHER_RDEV, 11, CAPACITY, 512)
    with pytest.raises(UnsafeRemovableTarget, match="by-id alias now refers to a different device"):
        harness.backend.write(harness.plan, harness.source)
    assert harness.commands == []


def test_private_node_that_opens_another_device_stops_before_repartitioning(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.kernel.by_rdev[DISK_RDEV] = BlockIdentity(DISK_RDEV, 99, CAPACITY, 512)
    with pytest.raises(UnsafeRemovableTarget, match="private device node now refers to a different device"):
        harness.backend.write(harness.plan, harness.source)
    assert harness.commands == []


def test_hotplug_replacement_during_partitioning_stops_before_format_and_blocks_invalidation(
    harness: Harness,
) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    node_dir = backend._locked[harness.device.device_id].node_dir
    harness.hooks["sfdisk"] = lambda _args: harness.kernel.hotplug_replace()
    with pytest.raises(UnsafeRemovableTarget, match="changed identity"):
        backend.write(harness.plan, harness.source)
    assert harness.names() == ["sfdisk"]
    before = harness.image.read_bytes()[:512 * 34]
    with pytest.raises(UnsafeRemovableTarget, match="refusing to invalidate another device"):
        backend.invalidate(harness.device, "hotplug")
    assert harness.image.read_bytes()[:512 * 34] == before
    assert backend._locked == {} and not node_dir.exists()
    assert "udisksctl" not in harness.names()


def test_vanished_private_node_is_rejected(harness: Harness) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    backend._locked[harness.device.device_id].disk_node.unlink()
    with pytest.raises(UnsafeRemovableTarget, match="private device node is missing"):
        backend.flush(harness.device)


@pytest.mark.parametrize(
    ("geometry", "message"),
    [
        ({"parent": OTHER_RDEV}, "does not belong to the locked disk"),
        ({"start": ESP_FIRST_LBA + 8}, "planned layout"),
        ({"size": PARTITION_SECTORS - 8}, "planned layout"),
        ({"diskseq": 99}, "does not belong to the locked disk"),
    ],
)
def test_created_partition_must_belong_to_locked_disk_and_plan(
    harness: Harness, geometry: dict[str, int], message: str
) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.kernel.partition_args = geometry
    with pytest.raises(UnsafeRemovableTarget, match=message):
        harness.backend.write(harness.plan, harness.source)
    assert "mkfs.vfat" not in harness.names()


def test_missing_created_partition_does_not_wait_on_udev(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.kernel.partition_args = None
    with pytest.raises(UnsafeRemovableTarget, match="did not expose the prepared EFI partition"):
        harness.backend.write(harness.plan, harness.source)
    assert harness.names() == ["sfdisk"]


def test_partition_node_retarget_between_format_and_mount_is_rejected(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.hooks["mkfs.vfat"] = lambda _args: harness.kernel.by_rdev.update(
        {PART_RDEV: BlockIdentity(PART_RDEV, 42, PARTITION_SECTORS * 512, 512)}
    )
    with pytest.raises(UnsafeRemovableTarget, match="does not belong to the locked disk"):
        harness.backend.write(harness.plan, harness.source)
    assert "mount" not in harness.names()


def test_mount_of_a_different_volume_is_unmounted_and_rejected_before_copy(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    original_run = harness.run

    def run(args: list[str], input_text: Optional[str] = None) -> str:
        result = original_run(args, input_text)
        if args[0] == "mount":
            harness.kernel.mounts[args[-1]] = OTHER_RDEV
        return result

    harness.backend._runner = run
    with pytest.raises(UnsafeRemovableTarget, match="not the locked disk's prepared EFI partition"):
        harness.backend.write(harness.plan, harness.source)
    assert not any(harness.volume.rglob("*"))
    assert harness.names()[-1] == "umount", "a mount that failed its check is still unmounted"
    assert harness.backend._mounts == {} and harness.kernel.mounts == {}
    assert not any(harness.mount_parent.iterdir())


def test_busy_unmount_does_not_mask_the_error_or_prevent_invalidation(harness: Harness) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    original_copy = backend._copy_boot_layout

    def failing_copy(source_dir: Path, mount: Path, plan: WritePlan) -> None:
        original_copy(source_dir, mount, plan)
        harness.fail.add("umount")
        raise OSError("disk full")

    backend._copy_boot_layout = failing_copy  # type: ignore[method-assign]
    with pytest.raises(OSError, match="disk full"):
        backend.write(harness.plan, harness.source)
    assert device_is_mounted(harness)
    backend.invalidate(harness.device, "copy failed")
    assert ["umount", "--lazy"] in [command[:2] for command in harness.commands]
    data = harness.image.read_bytes()
    assert data[:1024 * 1024] == bytes(1024 * 1024) and data[-1024 * 1024:] == bytes(1024 * 1024)
    assert harness.names()[-2:] == ["blockdev", "udisksctl"]
    assert backend._locked == {} and backend._mounts == {}


def device_is_mounted(harness: Harness) -> bool:
    return harness.device.device_id in harness.backend._mounts


def test_invalidation_skips_the_wipe_when_nothing_destructive_ran(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    marker = b"untouched"
    _patch(harness.image, 0, marker)
    harness.backend.invalidate(harness.device, "failed before sfdisk")
    assert harness.image.read_bytes()[:len(marker)] == marker
    assert harness.commands == [] and harness.backend._locked == {}


def test_eject_happens_while_the_lock_still_pins_the_device(harness: Harness) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    observed: list[bool] = []
    harness.hooks["udisksctl"] = lambda _args: observed.append(harness.device.device_id in backend._locked)
    backend.safe_eject(harness.device)
    assert observed == [True]
    backend.lock_and_dismount(harness.device)
    harness.kernel.aliases[str(harness.kernel.alias)] = BlockIdentity(OTHER_RDEV, 5, CAPACITY, 512)
    with pytest.raises(UnsafeRemovableTarget):
        backend.safe_eject(harness.device)
    assert observed == [True] and backend._locked == {}


# --- FAT32 and capacity limits before repartitioning -------------------------------


def test_fat32_per_file_limit_is_enforced_while_planning(tmp_path: Path, source: Path) -> None:
    oversized = source / "Recovery" / "BaseSystem.dmg"
    with oversized.open("wb") as handle:
        handle.truncate(FAT32_MAX_FILE_BYTES + 1)
    device = RemovableDevice(f"linux:by-id:{ALIAS}", "USB", 64 * 1024**3, False, True, False, serial="PIN001")
    with pytest.raises(UnsafeRemovableTarget, match="FAT32 per-file limit"):
        RemovableMediaWriter().dry_run(device, 1, source_dir=source, bindings=QUALIFIED)
    with oversized.open("r+b") as handle:
        handle.truncate(FAT32_MAX_FILE_BYTES)
    assert FAT32_MAX_FILE_BYTES == 2**32 - 1


def test_backend_rechecks_fat32_and_capacity_before_any_command(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    forged = WritePlan(
        harness.device, 1,
        expected_files=(MediaFileDigest("Recovery/BaseSystem.dmg", FAT32_MAX_FILE_BYTES + 1, "0" * 64),),
        bindings=QUALIFIED, source_validated=True,
    )
    with pytest.raises(UnsafeRemovableTarget, match="FAT32 per-file limit"):
        harness.backend.write(forged, harness.source)
    too_large = replace(forged, expected_files=(MediaFileDigest("EFI/big.bin", CAPACITY, "0" * 64),))
    with pytest.raises(UnsafeRemovableTarget, match="does not fit"):
        harness.backend.write(too_large, harness.source)
    unsafe = replace(forged, expected_files=(MediaFileDigest("../escape", 1, "0" * 64),))
    with pytest.raises(UnsafeRemovableTarget, match="unsafe relative path"):
        harness.backend.write(unsafe, harness.source)
    empty = replace(forged, expected_files=())
    with pytest.raises(UnsafeRemovableTarget, match="no payload files"):
        harness.backend.write(empty, harness.source)
    assert harness.commands == []


def test_backend_preflight_rejects_fat_unsafe_names_and_tiny_media(harness: Harness) -> None:
    def plan_with(*records: MediaFileDigest, capacity: int = CAPACITY) -> WritePlan:
        target = replace(harness.device, capacity_bytes=capacity)
        return WritePlan(target, 1, expected_files=records, bindings=QUALIFIED, source_validated=True)

    backend = harness.backend
    with pytest.raises(UnsafeRemovableTarget, match="collide on case-insensitive FAT32"):
        backend.preflight(plan_with(MediaFileDigest("EFI/a.efi", 1, "0" * 64), MediaFileDigest("EFI/A.EFI", 1, "0" * 64)))
    for bad in ("EFI/a:b.efi", "EFI/trailing.", "EFI/tab\tname"):
        with pytest.raises(UnsafeRemovableTarget, match="cannot be stored on FAT32"):
            backend.preflight(plan_with(MediaFileDigest(bad, 1, "0" * 64)))
    with pytest.raises(UnsafeRemovableTarget, match="too small for a valid FAT32"):
        backend.preflight(plan_with(MediaFileDigest("EFI/a.efi", 1, "0" * 64), capacity=32 * 1024 * 1024))
    backend.preflight(harness.plan)
    assert harness.commands == []


def test_adapter_runs_backend_preflight_before_locking(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from macloader.removable import LinuxRemovableAdapter

    monkeypatch.setattr(LinuxBlockDeviceBackend, "production_qualified", True)
    adapter = LinuxRemovableAdapter(advertised=True, enumerator=lambda: [harness.device], backend=harness.backend, platform="linux")
    locked: list[bool] = []
    monkeypatch.setattr(harness.backend, "lock_and_dismount", lambda _device: locked.append(True))
    oversized = WritePlan(
        harness.device, 1, expected_files=(MediaFileDigest("EFI/big.bin", CAPACITY, "0" * 64),),
        bindings=QUALIFIED, source_validated=True,
    )
    with pytest.raises(UnsafeRemovableTarget, match="does not fit"):
        adapter.write(oversized, harness.source)
    assert locked == []


@pytest.mark.skipif(shutil.which("mkfs.vfat") is None, reason="needs dosfstools")
@pytest.mark.parametrize("disk_mib", [80, 300, 1024])
def test_fat32_verifier_accepts_real_mkfs_track_rounding(tmp_path: Path, disk_mib: int) -> None:
    """mkfs.fat rounds the volume down to whole tracks on real partitions."""
    partition_sectors = LinuxBlockDeviceBackend._partition_sectors(disk_mib * 1024 * 1024 // 512)
    volume = tmp_path / "partition.img"
    with volume.open("wb") as handle:
        handle.truncate(partition_sectors * 512)
    formatted = subprocess.run(
        ["mkfs.vfat", "-F", "32", "-S", "512", "-n", "MACLOADER", str(volume)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert formatted.returncode == 0, formatted.stderr
    descriptor = os.open(volume, os.O_RDONLY)
    try:
        total = struct.unpack_from("<I", os.pread(descriptor, 512, 0), 32)[0]
        assert total <= partition_sectors
        verify_fat32_volume(descriptor, 0, partition_sectors)
    finally:
        os.close(descriptor)


# --- readback corruption -----------------------------------------------------------


def _written(harness: Harness) -> Harness:
    harness.backend.lock_and_dismount(harness.device)
    harness.backend.write(harness.plan, harness.source)
    return harness


def _flip(image: Path, offset: int) -> None:
    with image.open("r+b") as handle:
        handle.seek(offset)
        value = handle.read(1)[0]
        handle.seek(offset)
        handle.write(bytes([value ^ 0xFF]))


LAST = SECTORS - 1


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda image: _flip(image, 510), id="protective-mbr"),
        pytest.param(lambda image: _flip(image, 512 + 40), id="primary-gpt-header"),
        pytest.param(lambda image: _flip(image, LAST * 512 + 40), id="backup-gpt-header"),
        pytest.param(lambda image: _flip(image, 2 * 512 + 200), id="primary-gpt-entries"),
        pytest.param(lambda image: _flip(image, (LAST - GPT_ENTRY_SECTORS) * 512 + 200), id="backup-gpt-entries"),
        pytest.param(lambda image: write_gpt(image, _gpt_entries(type_guid=uuid.UUID(int=5))), id="partition-type"),
        pytest.param(lambda image: write_gpt(image, _gpt_entries(first=4096, count=PARTITION_SECTORS - 2048)), id="geometry"),
        pytest.param(
            lambda image: write_gpt(image, _gpt_entries()[:128] + _gpt_entries(first=100, count=10)[:128] + bytes(126 * 128)),
            id="extra-partition",
        ),
        pytest.param(lambda image: _flip(image, ESP_FIRST_LBA * 512 + 0x47), id="fat-label"),
        pytest.param(lambda image: _flip(image, ESP_FIRST_LBA * 512 + 32), id="fat-total-sectors"),
        pytest.param(lambda image: _flip(image, (ESP_FIRST_LBA + 6) * 512 + 100), id="fat-backup-boot"),
        pytest.param(lambda image: _flip(image, (ESP_FIRST_LBA + 1) * 512), id="fat-fsinfo"),
        pytest.param(lambda image: _flip(image, (ESP_FIRST_LBA + 32 + 1024) * 512), id="fat-second-table"),
    ],
)
def test_readback_rejects_layout_corruption(
    harness: Harness, corrupt: Callable[[Path], None]
) -> None:
    _written(harness)
    corrupt(harness.image)
    assert harness.backend.readback(harness.plan, harness.source) is False
    assert harness.kernel.cache_drops == 1
    # Layout verification fails before the volume is mounted for file checks.
    assert [command[0] for command in harness.commands].count("mount") == 1


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda volume: (volume / "EFI" / "OC" / "OpenCore.efi").write_bytes(b"OC"), id="payload-bytes"),
        pytest.param(lambda volume: (volume / "com.apple.recovery.boot" / "BaseSystem.dmg").unlink(), id="payload-missing"),
        pytest.param(lambda volume: (volume / "EFI" / "extra.efi").write_bytes(b"x"), id="unplanned-file"),
        pytest.param(lambda volume: (volume / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(b"boot!"), id="payload-size"),
    ],
)
def test_readback_verifies_every_payload_file_and_rejects_extras(
    harness: Harness, corrupt: Callable[[Path], None]
) -> None:
    _written(harness)
    corrupt(harness.volume)
    assert harness.backend.readback(harness.plan, harness.source) is False
    assert harness.backend._mounts == {}


def test_readback_stops_when_the_device_is_replaced(harness: Harness) -> None:
    _written(harness)
    harness.kernel.hotplug_replace()
    with pytest.raises(UnsafeRemovableTarget, match="changed identity"):
        harness.backend.readback(harness.plan, harness.source)


def test_invalidation_clears_both_gpt_copies_through_the_locked_descriptor(harness: Harness) -> None:
    _written(harness)
    harness.commands.clear()
    harness.backend.invalidate(harness.device, "synthetic readback failure")
    data = harness.image.read_bytes()
    assert data[:1024 * 1024] == bytes(1024 * 1024)
    assert data[-1024 * 1024:] == bytes(1024 * 1024)
    assert [command[0] for command in harness.commands] == ["blockdev", "udisksctl"]
    assert all(arg.startswith(str(harness.kernel.node_parent)) for arg in harness.device_args())
    assert harness.backend._locked == {}
    with pytest.raises(UnsafeRemovableTarget):
        fd = os.open(harness.image, os.O_RDONLY)
        try:
            verify_gpt_layout(fd, CAPACITY)
        finally:
            os.close(fd)


def test_layout_verifiers_accept_synthetic_media_and_reject_bad_geometry(tmp_path: Path) -> None:
    image = tmp_path / "image"
    with image.open("wb") as handle:
        handle.truncate(CAPACITY)
    write_gpt(image)
    write_fat32(image)
    fd = os.open(image, os.O_RDONLY)
    try:
        assert verify_gpt_layout(fd, CAPACITY).size_sectors == PARTITION_SECTORS
        verify_fat32_volume(fd, ESP_FIRST_LBA, PARTITION_SECTORS)
        with pytest.raises(UnsafeRemovableTarget, match="whole number"):
            verify_gpt_layout(fd, CAPACITY + 1)
        with pytest.raises(UnsafeRemovableTarget, match="boot parameters"):
            verify_fat32_volume(fd, ESP_FIRST_LBA, PARTITION_SECTORS - 1)
        with pytest.raises(UnsafeRemovableTarget, match="ended before"):
            verify_fat32_volume(fd, SECTORS, 1)
    finally:
        os.close(fd)
    write_fat32(image, size=60_000)  # too few clusters for FAT32
    fd = os.open(image, os.O_RDONLY)
    try:
        with pytest.raises(UnsafeRemovableTarget, match="too few clusters"):
            verify_fat32_volume(fd, ESP_FIRST_LBA, 60_000)
    finally:
        os.close(fd)


def test_physical_writer_remains_disabled() -> None:
    assert LinuxBlockDeviceBackend.production_qualified is False
    from macloader.removable import LinuxRemovableAdapter

    adapter = LinuxRemovableAdapter(advertised=True, enumerator=lambda: [], platform="linux")
    assert adapter.status.qualified is False
    assert adapter.writer().destructive_write is None


def test_real_probe_reads_sysfs_geometry_and_rejects_non_block_files(tmp_path: Path) -> None:
    probe = LinuxBlockProbe()
    probe.sysfs_block_root = tmp_path / "dev-block"
    disk_dir = tmp_path / "devices" / "sdb"
    part_dir = disk_dir / "sdb1"
    part_dir.mkdir(parents=True)
    (disk_dir / "dev").write_text("8:16\n", encoding="ascii")
    (part_dir / "partition").write_text("1\n", encoding="ascii")
    (part_dir / "start").write_text("2048\n", encoding="ascii")
    (part_dir / "size").write_text("4096\n", encoding="ascii")
    probe.sysfs_block_root.mkdir()
    (probe.sysfs_block_root / "8:17").symlink_to(part_dir)
    (probe.sysfs_block_root / "8:16").symlink_to(disk_dir)

    assert probe.partition_geometry(PART_RDEV) == PartitionGeometry(DISK_RDEV, 1, 2048, 4096)
    assert probe.is_partition(PART_RDEV) and not probe.is_partition(DISK_RDEV)
    with pytest.raises(UnsafeRemovableTarget, match="partition geometry"):
        probe.partition_geometry(DISK_RDEV)

    regular = tmp_path / "not-a-device"
    regular.write_bytes(b"x")
    with pytest.raises(UnsafeRemovableTarget, match="not a physical block device"):
        probe.identity_of_path(regular)
    assert probe.mounted_device(tmp_path) == os.stat(tmp_path).st_dev
    descriptor = probe.open_locked(regular)
    try:
        with pytest.raises(OSError):
            probe.open_locked(regular)  # the flock is exclusive
    finally:
        os.close(descriptor)


def test_real_probe_fails_closed_without_fcntl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters_module, "_fcntl", None)
    probe = LinuxBlockProbe()
    with pytest.raises(UnsafeRemovableTarget, match="locking is unavailable"):
        probe.open_locked(tmp_path)
    with pytest.raises(UnsafeRemovableTarget, match="identity checks are unavailable"):
        probe.identity_of_fd(0)
    probe.drop_cache(0)
