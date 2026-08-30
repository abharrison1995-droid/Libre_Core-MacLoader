"""Unit tests for privacy sanitization."""

from macloader.detection.sanitize import sanitize_hardware_snapshot
from macloader.domain.hardware import (
    HardwareSnapshot,
    NetworkInfo,
    PciDevice,
    StorageInfo,
)


def test_sanitization_redacts_private_identifiers() -> None:
    snapshot = HardwareSnapshot(
        manufacturer="LENOVO",
        product_name="20L7CTO1WW",
        product_version="ThinkPad T480s",
        machine_type="20L7",
        serial_number="PF1ABCDE",
        uuid="12345678-1234-1234-1234-123456789abc",
        ethernet=[
            NetworkInfo(
                name="Intel I219-LM",
                kind="ethernet",
                pci=PciDevice(vendor_id="8086", device_id="15d7"),
                mac_address="a1:b2:c3:d4:e5:f6",
            )
        ],
        storage=[
            StorageInfo(
                model="WD Black NVMe",
                kind="nvme",
                serial="WD-WDS512G1X0C-00ENX0_18345678",
                pci=PciDevice(vendor_id="15b7", device_id="5002"),
            )
        ],
        raw_evidence={
            "dmi": {
                "serial": "PF1ABCDE",
                "uuid": "12345678-1234-1234-1234-123456789abc",
            },
            "ifconfig": "eth0: HWaddr a1:b2:c3:d4:e5:f6",
        },
    )

    sanitized = sanitize_hardware_snapshot(snapshot)

    # Invariants: PCI IDs must remain intact
    assert sanitized.ethernet[0].pci is not None
    assert sanitized.ethernet[0].pci.canonical_id == "8086:15d7"
    assert sanitized.storage[0].pci is not None
    assert sanitized.storage[0].pci.canonical_id == "15b7:5002"

    # Invariants: Serial numbers, UUIDs, and MAC addresses must be redacted
    assert sanitized.serial_number == "[REDACTED-SERIAL]"
    assert sanitized.uuid == "[REDACTED-UUID]"
    assert sanitized.ethernet[0].mac_address == "xx:xx:xx:xx:xx:xx"
    assert sanitized.storage[0].serial == "[REDACTED-STORAGE-SERIAL]"

    # Raw evidence must also be sanitized
    assert sanitized.raw_evidence["dmi"]["serial"] == "[REDACTED-SERIAL]"
    assert sanitized.raw_evidence["dmi"]["uuid"] == "[REDACTED-UUID]"
    assert "a1:b2:c3:d4:e5:f6" not in str(sanitized.raw_evidence)
    assert "12345678-1234-1234-1234-123456789abc" not in str(sanitized.raw_evidence)
