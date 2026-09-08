"""Unit tests for removable media writer safety guards, hot-swap protection,
disposable image adapter, and recovery asset matrix verification.
"""

from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import pytest

from macloader.exceptions import ArtifactDownloadError
from macloader.recovery import (
    RecoveryAcquirer,
    RecoveryAsset,
    SUPPORTED_RECOVERY_MATRIX,
    validate_recovery_product_version,
    verify_recovery_integrity,
)
from macloader.removable import (
    DisposableImageAdapter,
    RemovableDevice,
    RemovableMediaWriter,
    UnsafeRemovableTarget,
    WritePlan,
)


@pytest.fixture
def valid_source_efi(tmp_path: Path) -> Path:
    """Fixture creating a valid EFI staging structure with bootloader and OC binaries."""
    source_dir = tmp_path / "valid_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    efi_dir = source_dir / "EFI"
    boot_dir = efi_dir / "BOOT"
    oc_dir = efi_dir / "OC"
    boot_dir.mkdir(parents=True)
    oc_dir.mkdir(parents=True)

    boot_bin = boot_dir / "BOOTx64.efi"
    boot_bin.write_bytes(b"\x4d\x5a\x90\x00_FAKE_BOOTX64_BIN")

    oc_bin = oc_dir / "OpenCore.efi"
    oc_bin.write_bytes(b"\x4d\x5a\x90\x00_FAKE_OPENCORE_BIN")

    config = oc_dir / "config.plist"
    config.write_text("<?xml version='1.0'?><plist version='1.0'></plist>", encoding="utf-8")
    return source_dir


@pytest.fixture
def safe_device() -> RemovableDevice:
    """Fixture creating a valid removable USB target device."""
    return RemovableDevice(
        device_id="DISK_USB_01",
        model="SanDisk Ultra USB 3.0",
        capacity_bytes=16 * 1024 * 1024 * 1024,
        is_removable=True,
        is_system_disk=False,
        mounted=False,
        read_only=False,
        serial="SD12345678",
    )


# =========================================================================
# 1. RemovableMediaWriter Safety Guards
# =========================================================================

def test_writer_refuses_empty_device_id(valid_source_efi: Path) -> None:
    dev = RemovableDevice(
        device_id="",
        model="USB Drive",
        capacity_bytes=8 * 1024 * 1024 * 1024,
        is_removable=True,
        is_system_disk=False,
        mounted=False,
    )
    writer = RemovableMediaWriter()
    with pytest.raises(UnsafeRemovableTarget, match="device ID is missing"):
        writer.dry_run(dev, 1024 * 1024)


def test_writer_refuses_non_removable_device(safe_device: RemovableDevice) -> None:
    non_removable = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=safe_device.capacity_bytes,
        is_removable=False,
        is_system_disk=False,
        mounted=False,
    )
    writer = RemovableMediaWriter()
    with pytest.raises(UnsafeRemovableTarget, match="not removable"):
        writer.dry_run(non_removable, 1024 * 1024)


def test_writer_refuses_system_disk(safe_device: RemovableDevice) -> None:
    system_disk = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=safe_device.capacity_bytes,
        is_removable=True,
        is_system_disk=True,
        mounted=False,
    )
    writer = RemovableMediaWriter()
    with pytest.raises(UnsafeRemovableTarget, match="Refusing to modify the system disk"):
        writer.dry_run(system_disk, 1024 * 1024)


def test_writer_refuses_mounted_device(safe_device: RemovableDevice) -> None:
    mounted_disk = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=safe_device.capacity_bytes,
        is_removable=True,
        is_system_disk=False,
        mounted=True,
    )
    writer = RemovableMediaWriter()
    with pytest.raises(UnsafeRemovableTarget, match="Target is mounted or busy"):
        writer.dry_run(mounted_disk, 1024 * 1024)


def test_writer_refuses_read_only_device(safe_device: RemovableDevice) -> None:
    ro_disk = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=safe_device.capacity_bytes,
        is_removable=True,
        is_system_disk=False,
        mounted=False,
        read_only=True,
    )
    writer = RemovableMediaWriter()
    with pytest.raises(UnsafeRemovableTarget, match="Target device is read-only"):
        writer.dry_run(ro_disk, 1024 * 1024)


def test_writer_refuses_zero_or_negative_capacity(safe_device: RemovableDevice) -> None:
    zero_cap = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=0,
        is_removable=True,
        is_system_disk=False,
        mounted=False,
    )
    writer = RemovableMediaWriter()
    with pytest.raises(UnsafeRemovableTarget, match="Target capacity is invalid or zero"):
        writer.dry_run(zero_cap, 1024 * 1024)


def test_writer_refuses_insufficient_capacity(safe_device: RemovableDevice) -> None:
    small_disk = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=512 * 1024 * 1024,
        is_removable=True,
        is_system_disk=False,
        mounted=False,
    )
    writer = RemovableMediaWriter()
    with pytest.raises(UnsafeRemovableTarget, match="Target capacity is insufficient"):
        writer.dry_run(small_disk, 1024 * 1024 * 1024)


# =========================================================================
# 2. Source Directory & EFI Tree Validation
# =========================================================================

def test_writer_refuses_missing_source(safe_device: RemovableDevice, tmp_path: Path) -> None:
    writer = RemovableMediaWriter()
    missing = tmp_path / "nonexistent_source"
    with pytest.raises(UnsafeRemovableTarget, match="does not exist"):
        writer.dry_run(safe_device, 1024, source_dir=missing)


def test_writer_refuses_file_as_source(safe_device: RemovableDevice, tmp_path: Path) -> None:
    writer = RemovableMediaWriter()
    file_path = tmp_path / "some_file.txt"
    file_path.write_text("not a directory")
    with pytest.raises(UnsafeRemovableTarget, match="not a directory"):
        writer.dry_run(safe_device, 1024, source_dir=file_path)


def test_writer_refuses_empty_source(safe_device: RemovableDevice, tmp_path: Path) -> None:
    writer = RemovableMediaWriter()
    empty_source = tmp_path / "empty_source"
    empty_source.mkdir()

    with pytest.raises(UnsafeRemovableTarget, match="missing required bootloader"):
        writer.dry_run(safe_device, 1024, source_dir=empty_source)


def test_writer_refuses_incomplete_efi_source(safe_device: RemovableDevice, tmp_path: Path) -> None:
    writer = RemovableMediaWriter()
    broken_source = tmp_path / "broken_efi"
    broken_source.mkdir()
    efi_dir = broken_source / "EFI"
    efi_dir.mkdir()
    (efi_dir / "BOOT").mkdir()
    # Missing BOOTx64.efi and OpenCore.efi
    with pytest.raises(UnsafeRemovableTarget, match="missing required bootloader"):
        writer.dry_run(safe_device, 1024, source_dir=broken_source)


def test_writer_refuses_custom_source_validator_failure(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    writer = RemovableMediaWriter(source_validator=lambda path: False)
    with pytest.raises(UnsafeRemovableTarget, match="failed EFI/Recovery validation"):
        writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)


def test_writer_handles_source_validator_exception(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    def failing_validator(path: Path) -> bool:
        raise RuntimeError("Validator crashed")

    writer = RemovableMediaWriter(source_validator=failing_validator)
    with pytest.raises(UnsafeRemovableTarget, match="Source validation raised an error"):
        writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)


def test_writer_requires_readback_verifier_before_destructive_write(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    writes: list[bool] = []
    writer = RemovableMediaWriter(destructive_write=lambda _plan, _source: writes.append(True))
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="No readback verifier is configured"):
        writer.write(plan, valid_source_efi, confirmation)
    assert writes == []


def test_writer_requires_stable_identity_before_destructive_write(
    valid_source_efi: Path,
) -> None:
    device = RemovableDevice(
        device_id="DISK_USB_NO_SERIAL",
        model="USB Drive",
        capacity_bytes=16 * 1024 * 1024 * 1024,
        is_removable=True,
        is_system_disk=False,
        mounted=False,
        serial=None,
    )
    writes: list[bool] = []
    writer = RemovableMediaWriter(
        destructive_write=lambda _plan, _source: writes.append(True),
        readback_verifier=lambda _plan, _source: True,
    )
    plan = writer.dry_run(device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {device.device_id} {device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="stable device serial"):
        writer.write(plan, valid_source_efi, confirmation)
    assert writes == []


def test_writer_normalizes_keyboard_interrupt(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    def interrupt(_plan: WritePlan, _source: Path) -> None:
        raise KeyboardInterrupt()

    writer = RemovableMediaWriter(
        destructive_write=interrupt,
        readback_verifier=lambda _plan, _source: True,
    )
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="Write operation failed or was interrupted"):
        writer.write(plan, valid_source_efi, confirmation)


# =========================================================================
# 3. Confirmation Safety & Hot-Swap / Re-Enumeration Guards
# =========================================================================

def test_writer_refuses_wrong_confirmation(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    writer = RemovableMediaWriter(destructive_write=lambda p, s: None)
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)

    # Wrong device ID
    with pytest.raises(UnsafeRemovableTarget, match="Confirmation does not match"):
        writer.write(plan, valid_source_efi, f"WRITE DISK_WRONG {safe_device.capacity_bytes}")

    # Wrong capacity
    with pytest.raises(UnsafeRemovableTarget, match="Confirmation does not match"):
        writer.write(plan, valid_source_efi, f"WRITE {safe_device.device_id} 99999")

    # Generic YES
    with pytest.raises(UnsafeRemovableTarget, match="Confirmation does not match"):
        writer.write(plan, valid_source_efi, "YES")


def test_writer_refuses_missing_platform_write_adapter(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    writer = RemovableMediaWriter(destructive_write=None)
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"
    with pytest.raises(UnsafeRemovableTarget, match="No platform write adapter is configured"):
        writer.write(plan, valid_source_efi, confirmation)


def test_writer_re_enumeration_detects_removed_device(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    writer = RemovableMediaWriter(
        destructive_write=lambda p, s: None,
        enumerator=lambda: [],  # Target disappeared!
    )
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="is no longer present"):
        writer.write(plan, valid_source_efi, confirmation)


def test_writer_re_enumeration_detects_ambiguous_devices(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    writer = RemovableMediaWriter(
        destructive_write=lambda p, s: None,
        enumerator=lambda: [safe_device, safe_device],  # Duplicate!
    )
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="Ambiguous target devices found"):
        writer.write(plan, valid_source_efi, confirmation)


def test_writer_re_enumeration_detects_capacity_change(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    swapped_device = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=safe_device.capacity_bytes * 2,  # Swapped with different drive
        is_removable=True,
        is_system_disk=False,
        mounted=False,
        serial=safe_device.serial,
    )
    writer = RemovableMediaWriter(
        destructive_write=lambda p, s: None,
        enumerator=lambda: [swapped_device],
    )
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="Target device capacity changed.*possible hot-swap"):
        writer.write(plan, valid_source_efi, confirmation)


def test_writer_re_enumeration_detects_model_or_serial_change(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    swapped_device = RemovableDevice(
        device_id=safe_device.device_id,
        model="Kingston DataTraveler",  # Different physical drive assigned same ID
        capacity_bytes=safe_device.capacity_bytes,
        is_removable=True,
        is_system_disk=False,
        mounted=False,
        serial="DIFFERENT_SERIAL",
    )
    writer = RemovableMediaWriter(
        destructive_write=lambda p, s: None,
        enumerator=lambda: [swapped_device],
    )
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="Target device identity changed.*possible hot-swap"):
        writer.write(plan, valid_source_efi, confirmation)


def test_writer_re_enumeration_detects_device_mounted_between_plan_and_write(
    safe_device: RemovableDevice, valid_source_efi: Path
) -> None:
    now_mounted = RemovableDevice(
        device_id=safe_device.device_id,
        model=safe_device.model,
        capacity_bytes=safe_device.capacity_bytes,
        is_removable=True,
        is_system_disk=False,
        mounted=True,  # OS auto-mounted device in the meantime!
        serial=safe_device.serial,
    )
    writer = RemovableMediaWriter(
        destructive_write=lambda p, s: None,
        enumerator=lambda: [now_mounted],
    )
    plan = writer.dry_run(safe_device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {safe_device.device_id} {safe_device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="Target is mounted or busy"):
        writer.write(plan, valid_source_efi, confirmation)


# =========================================================================
# 4. DisposableImageAdapter: Write Execution, Interruption & Readback Verification
# =========================================================================

def test_disposable_image_adapter_positive_write_and_readback(
    valid_source_efi: Path, tmp_path: Path
) -> None:
    target_image_dir = tmp_path / "mock_usb_mount"
    target_image_dir.mkdir()

    adapter = DisposableImageAdapter(target_image_dir)
    writer = RemovableMediaWriter(
        destructive_write=adapter.write_payload,
        readback_verifier=adapter.verify_readback,
    )

    device = adapter.create_mock_device(
        device_id="DISPOSABLE_USB_IMAGE",
        capacity_bytes=32 * 1024 * 1024 * 1024,
    )
    plan = writer.dry_run(device, 1024 * 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {device.device_id} {device.capacity_bytes}"

    # Perform write and readback
    writer.write(plan, valid_source_efi, confirmation)

    # Validate that files were written and are structurally intact
    dest_efi = target_image_dir / "EFI"
    assert (dest_efi / "BOOT" / "BOOTx64.efi").is_file()
    assert (dest_efi / "OC" / "OpenCore.efi").is_file()
    assert (dest_efi / "OC" / "config.plist").is_file()

    # Direct adapter verification check
    assert adapter.verify_readback(plan, valid_source_efi) is True


def test_disposable_image_adapter_handles_write_interruption(
    valid_source_efi: Path, tmp_path: Path
) -> None:
    target_image_dir = tmp_path / "mock_usb_mount"
    target_image_dir.mkdir()

    adapter = DisposableImageAdapter(target_image_dir, simulate_interruption=True)
    writer = RemovableMediaWriter(
        destructive_write=adapter.write_payload,
        readback_verifier=adapter.verify_readback,
    )

    device = adapter.create_mock_device()
    plan = writer.dry_run(device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {device.device_id} {device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="Write operation failed or was interrupted"):
        writer.write(plan, valid_source_efi, confirmation)


def test_disposable_image_adapter_detects_corrupted_readback(
    valid_source_efi: Path, tmp_path: Path
) -> None:
    target_image_dir = tmp_path / "mock_usb_mount"
    target_image_dir.mkdir()

    adapter = DisposableImageAdapter(target_image_dir, corrupt_readback=True)
    writer = RemovableMediaWriter(
        destructive_write=adapter.write_payload,
        readback_verifier=adapter.verify_readback,
    )

    device = adapter.create_mock_device()
    plan = writer.dry_run(device, 1024, source_dir=valid_source_efi)
    confirmation = f"WRITE {device.device_id} {device.capacity_bytes}"

    with pytest.raises(UnsafeRemovableTarget, match="Readback verification failed after write"):
        writer.write(plan, valid_source_efi, confirmation)


def test_disposable_image_adapter_rejects_extra_payload_files(
    valid_source_efi: Path, tmp_path: Path
) -> None:
    target_image_dir = tmp_path / "mock_usb_mount"
    adapter = DisposableImageAdapter(target_image_dir)
    device = adapter.create_mock_device()
    plan = WritePlan(device, 1024, partitions=["GPT", "EFI", "Recovery"])
    adapter.write(plan, valid_source_efi)
    (target_image_dir / "EFI" / "unexpected.bin").write_bytes(b"unexpected")

    assert adapter.verify_readback(plan, valid_source_efi) is False


# =========================================================================
# 5. Recovery Asset Matrix & Product/Build Version Validation
# =========================================================================

def test_recovery_matrix_definitions() -> None:
    assert "sequoia" in SUPPORTED_RECOVERY_MATRIX
    assert "sonoma" in SUPPORTED_RECOVERY_MATRIX
    assert "tahoe" in SUPPORTED_RECOVERY_MATRIX

    assert SUPPORTED_RECOVERY_MATRIX["sequoia"]["major_version"] == 15
    assert SUPPORTED_RECOVERY_MATRIX["sequoia"]["build_prefix"] == "24"
    assert "Lenovo ThinkPad T480s" in SUPPORTED_RECOVERY_MATRIX["sequoia"]["supported_models"]

    assert SUPPORTED_RECOVERY_MATRIX["sonoma"]["major_version"] == 14
    assert SUPPORTED_RECOVERY_MATRIX["sonoma"]["build_prefix"] == "23"
    assert "Lenovo ThinkPad T480s" in SUPPORTED_RECOVERY_MATRIX["sonoma"]["supported_models"]


def test_validate_recovery_product_version_positives() -> None:
    assert validate_recovery_product_version("InstallAssistant", "24A348", expected_macos="sequoia")
    assert validate_recovery_product_version("BaseSystem", "23F79", expected_macos="sonoma")
    assert validate_recovery_product_version("RecoveryImage", "26A100", expected_macos="tahoe")
    # Without expected_macos constraint
    assert validate_recovery_product_version("InstallAssistant", "24A348")


def test_validate_recovery_product_version_negatives() -> None:
    # Invalid characters
    assert not validate_recovery_product_version("../bad", "24A348")
    assert not validate_recovery_product_version("product*", "24A348")
    assert not validate_recovery_product_version("", "24A348")

    # Invalid build strings
    assert not validate_recovery_product_version("BaseSystem", "")
    assert not validate_recovery_product_version("BaseSystem", "invalid_build")
    assert not validate_recovery_product_version("BaseSystem", "15A")  # Too short

    # Mismatched macOS version
    assert not validate_recovery_product_version("InstallAssistant", "24A348", expected_macos="sonoma")
    assert not validate_recovery_product_version("BaseSystem", "23F79", expected_macos="sequoia")
    assert not validate_recovery_product_version("BaseSystem", "24A348", expected_macos="unknown_os")


def test_recovery_acquirer_enforces_product_and_build_regex(tmp_path: Path) -> None:
    acquirer = RecoveryAcquirer()
    dest = tmp_path / "dest.dmg"

    # Malformed product
    with pytest.raises(ArtifactDownloadError, match="invalid characters"):
        acquirer.download(
            RecoveryAsset("../../bad_product", "24A348", "https://osrecovery.apple.com/rec", "0" * 64, 100),
            dest,
        )

    # Malformed build
    with pytest.raises(ArtifactDownloadError, match="invalid characters"):
        acquirer.download(
            RecoveryAsset("BaseSystem", "../../bad_build", "https://osrecovery.apple.com/rec", "0" * 64, 100),
            dest,
        )


def test_recovery_acquirer_enforces_supported_matrix(tmp_path: Path) -> None:
    payload = b"recovery"

    def transport(_url: str, path: Path) -> None:
        path.write_bytes(payload)

    asset = RecoveryAsset(
        product="InstallAssistant",
        build="27A100",
        source_url="https://osrecovery.apple.com/rec",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )

    with pytest.raises(ArtifactDownloadError, match="not supported"):
        RecoveryAcquirer(transport=transport).download(asset, tmp_path / "Recovery.dmg")

    with pytest.raises(ArtifactDownloadError, match="not supported"):
        RecoveryAcquirer(expected_macos="sonoma", transport=transport).download(
            RecoveryAsset("InstallAssistant", "24A348", asset.source_url, asset.sha256, asset.size_bytes),
            tmp_path / "Recovery-sonoma.dmg",
        )


def test_verify_recovery_integrity(tmp_path: Path) -> None:
    payload = b"Apple macOS Recovery Payload Chunk Data"
    asset_hash = hashlib.sha256(payload).hexdigest()
    asset = RecoveryAsset(
        product="BaseSystem",
        build="24A348",
        source_url="https://osrecovery.apple.com/rec",
        sha256=asset_hash,
        size_bytes=len(payload),
    )

    valid_file = tmp_path / "BaseSystem.dmg"
    valid_file.write_bytes(payload)

    # Positive check
    assert verify_recovery_integrity(asset, valid_file) is True

    # Corrupted size
    corrupt_size_file = tmp_path / "corrupt_size.dmg"
    corrupt_size_file.write_bytes(payload + b"extra")
    assert verify_recovery_integrity(asset, corrupt_size_file) is False

    # Corrupted hash (same size)
    corrupt_hash_file = tmp_path / "corrupt_hash.dmg"
    corrupted_data = b"Bpple macOS Recovery Payload Chunk Data"  # 1 byte flipped
    assert len(corrupted_data) == len(payload)
    corrupt_hash_file.write_bytes(corrupted_data)
    assert verify_recovery_integrity(asset, corrupt_hash_file) is False

    # Nonexistent file
    assert verify_recovery_integrity(asset, tmp_path / "does_not_exist.dmg") is False

    # Unsafe metadata is rejected before reading the target.
    assert verify_recovery_integrity(
        RecoveryAsset("BaseSystem", "24A348", asset.source_url, "bad", len(payload)), valid_file
    ) is False
    assert verify_recovery_integrity(
        RecoveryAsset("BaseSystem", "24A348", asset.source_url, asset_hash, 0), valid_file
    ) is False
