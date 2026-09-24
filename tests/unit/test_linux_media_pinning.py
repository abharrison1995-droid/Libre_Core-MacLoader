"""Deterministic identity-pinning, race and corruption tests for the Linux writer.

A fake kernel models the locked descriptor, attachment-unique paths, by-id
aliases, partitions and mounts.  Commands are emulated in pure Python against
a disposable image file, so these tests never open a host block device.
"""

from dataclasses import replace
import os
from pathlib import Path
import shutil
import struct
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
CAPACITY = 64 * 1024 * 1024 + 512
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
    def __init__(self, tmp_path: Path, image: Path, by_id_root: Path) -> None:
        self.by_diskseq_root = tmp_path / "by-diskseq"
        self.image = image
        self.disk = BlockIdentity(DISK_RDEV, 7, CAPACITY, 512)
        self.held = self.disk
        self.alias = by_id_root / ALIAS
        self.paths: dict[str, BlockIdentity] = {
            str(self.alias): self.disk,
            str(self.by_diskseq_root / "7"): self.disk,
        }
        self.geometry: dict[int, PartitionGeometry] = {}
        self.mounts: dict[str, int] = {}
        self.cache_drops = 0
        self.lock_error: Optional[OSError] = None

    def open_locked(self, path: Path) -> int:
        if self.lock_error is not None:
            raise self.lock_error
        if str(path) not in self.paths:
            raise FileNotFoundError(path)
        return os.open(self.image, os.O_RDWR)

    def identity_of_fd(self, descriptor: int) -> BlockIdentity:
        return self.held

    def identity_of_path(self, path: Path) -> BlockIdentity:
        try:
            return self.paths[str(path)]
        except KeyError:
            raise FileNotFoundError(path) from None

    def partition_geometry(self, rdev: int) -> PartitionGeometry:
        return self.geometry[rdev]

    def is_partition(self, rdev: int) -> bool:
        return rdev in self.geometry

    def drop_cache(self, descriptor: int) -> None:
        self.cache_drops += 1

    def mounted_device(self, mount: Path) -> int:
        return self.mounts[str(mount)]

    def create_partition(self, start: int = ESP_FIRST_LBA, size: int = PARTITION_SECTORS, parent: int = DISK_RDEV) -> None:
        self.geometry[PART_RDEV] = PartitionGeometry(parent, 1, start, size)
        self.paths[str(self.by_diskseq_root / "7-part1")] = BlockIdentity(PART_RDEV, self.disk.diskseq, size * 512, 512)

    def hotplug_replace(self) -> None:
        """Model unplug + replug: same alias and dev_t, new attachment sequence."""
        replacement = replace(self.disk, diskseq=self.disk.diskseq + 1)
        self.held = replacement
        self.paths = {str(self.alias): replacement, str(self.by_diskseq_root / "8"): replacement}


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
        if args[0] == "sfdisk":
            write_gpt(self.image)
        elif args[0] == "blockdev" and args[1] == "--rereadpt":
            self.kernel.create_partition()
        elif args[0] == "mkfs.vfat":
            write_fat32(self.image)
            shutil.rmtree(self.volume)
            self.volume.mkdir()
        elif args[0] == "mount":
            mount = Path(args[-1])
            shutil.copytree(self.volume, mount, dirs_exist_ok=True)
            self.kernel.mounts[str(mount)] = self.kernel.paths[args[-2]].rdev
        elif args[0] == "umount":
            mount = Path(args[-1])
            shutil.rmtree(self.volume)
            shutil.copytree(mount, self.volume)
            for child in mount.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        return ""

    def device_args(self) -> list[str]:
        return [arg for command in self.commands for arg in command if arg.startswith(str(self.tmp_path))]


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


def test_every_media_command_uses_the_locked_attachment_path(harness: Harness) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    backend.write(harness.plan, harness.source)
    backend.flush(harness.device)
    assert backend.readback(harness.plan, harness.source) is True
    backend.safe_eject(harness.device)

    assert [command[0] for command in harness.commands] == [
        "sfdisk", "blockdev", "mkfs.vfat", "mount", "umount", "blockdev", "mount", "umount", "blockdev", "udisksctl",
    ]
    pinned_root = str(harness.kernel.by_diskseq_root)
    device_args = [arg for arg in harness.device_args() if not arg.startswith(str(harness.mount_parent))]
    assert device_args and all(arg.startswith(pinned_root) for arg in device_args)
    assert not any(ALIAS in arg for command in harness.commands for arg in command)
    assert harness.kernel.cache_drops == 1
    assert backend._locked == {} and backend._mounts == {}
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


def test_lock_rejects_alias_retargeted_between_open_and_verification(harness: Harness) -> None:
    kernel = harness.kernel
    kernel.paths[str(kernel.alias)] = BlockIdentity(OTHER_RDEV, 3, CAPACITY, 512)
    with pytest.raises(UnsafeRemovableTarget, match="by-id alias now refers to a different device"):
        harness.backend.lock_and_dismount(harness.device)
    assert harness.backend._locked == {}


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
    harness.kernel.paths[str(harness.kernel.alias)] = BlockIdentity(OTHER_RDEV, 11, CAPACITY, 512)
    with pytest.raises(UnsafeRemovableTarget, match="by-id alias now refers to a different device"):
        harness.backend.write(harness.plan, harness.source)
    assert harness.commands == []


def test_attachment_path_retarget_stops_before_repartitioning(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.kernel.paths[str(harness.kernel.by_diskseq_root / "7")] = BlockIdentity(OTHER_RDEV, 7, CAPACITY, 512)
    with pytest.raises(UnsafeRemovableTarget, match="attachment path now refers to a different device"):
        harness.backend.write(harness.plan, harness.source)
    assert harness.commands == []


def test_hotplug_replacement_during_partitioning_stops_before_format_and_blocks_invalidation(
    harness: Harness,
) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    harness.hooks["sfdisk"] = lambda _args: harness.kernel.hotplug_replace()
    with pytest.raises(UnsafeRemovableTarget, match="changed identity"):
        backend.write(harness.plan, harness.source)
    assert [command[0] for command in harness.commands] == ["sfdisk"]
    before = harness.image.read_bytes()[:512 * 34]
    with pytest.raises(UnsafeRemovableTarget, match="refusing to invalidate another device"):
        backend.invalidate(harness.device, "hotplug")
    assert harness.image.read_bytes()[:512 * 34] == before
    assert backend._locked == {}


def test_vanished_attachment_path_is_rejected(harness: Harness) -> None:
    backend = harness.backend
    backend.lock_and_dismount(harness.device)
    del harness.kernel.paths[str(harness.kernel.by_diskseq_root / "7")]
    with pytest.raises(UnsafeRemovableTarget, match="attachment path is missing"):
        backend.flush(harness.device)


@pytest.mark.parametrize(
    ("geometry", "message"),
    [
        ({"parent": OTHER_RDEV}, "does not belong to the locked disk"),
        ({"start": ESP_FIRST_LBA + 8}, "planned layout"),
        ({"size": PARTITION_SECTORS - 8}, "planned layout"),
    ],
)
def test_created_partition_must_belong_to_locked_disk_and_plan(
    harness: Harness, geometry: dict[str, int], message: str
) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.hooks["blockdev"] = lambda _args: None
    original = harness.kernel.create_partition

    def create_wrong(*_args: object, **_kwargs: object) -> None:
        original(**geometry)

    harness.kernel.create_partition = create_wrong  # type: ignore[method-assign]
    with pytest.raises(UnsafeRemovableTarget, match=message):
        harness.backend.write(harness.plan, harness.source)
    assert "mkfs.vfat" not in [command[0] for command in harness.commands]


def test_partition_from_another_attachment_is_rejected(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)

    def foreign_partition(_args: list[str]) -> None:
        harness.kernel.geometry[PART_RDEV] = PartitionGeometry(DISK_RDEV, 1, ESP_FIRST_LBA, PARTITION_SECTORS)
        harness.kernel.paths[str(harness.kernel.by_diskseq_root / "7-part1")] = BlockIdentity(
            PART_RDEV, 99, PARTITION_SECTORS * 512, 512
        )

    harness.hooks["blockdev"] = foreign_partition
    harness.kernel.create_partition = lambda *_a, **_k: None  # type: ignore[method-assign]
    with pytest.raises(UnsafeRemovableTarget, match="does not belong to the locked disk"):
        harness.backend.write(harness.plan, harness.source)


def test_missing_created_partition_is_rejected(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)
    harness.kernel.create_partition = lambda *_a, **_k: None  # type: ignore[method-assign]
    with pytest.raises(UnsafeRemovableTarget, match="did not expose the prepared EFI partition"):
        harness.backend.write(harness.plan, harness.source)
    assert [command[0] for command in harness.commands] == ["sfdisk", "blockdev"]


def test_mount_of_a_different_volume_is_rejected_before_copy(harness: Harness) -> None:
    harness.backend.lock_and_dismount(harness.device)

    def wrong_mount(args: list[str]) -> None:
        harness.kernel.mounts[args[-1]] = OTHER_RDEV

    original_run = harness.run

    def run(args: list[str], input_text: Optional[str] = None) -> str:
        result = original_run(args, input_text)
        if args[0] == "mount":
            wrong_mount(args)
        return result

    harness.backend._runner = run
    with pytest.raises(UnsafeRemovableTarget, match="not the locked disk's prepared EFI partition"):
        harness.backend.write(harness.plan, harness.source)
    assert not any(harness.volume.rglob("*"))
    assert harness.backend._mounts == {}


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
    assert all(arg.startswith(str(harness.kernel.by_diskseq_root)) for arg in harness.device_args())
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
