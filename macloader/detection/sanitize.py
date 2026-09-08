"""Privacy sanitization utilities for hardware snapshots and test fixtures."""

import re
from typing import Any, Dict

from macloader.domain.hardware import HardwareSnapshot


# Regex pattern to match standard MAC addresses
MAC_ADDRESS_REGEX = re.compile(r"\b([0-9a-fA-F]{2}[:-]){5}([0-9a-fA-F]{2})\b")
# Regex pattern to match standard UUIDs
UUID_REGEX = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def _sanitize_string(text: str) -> str:
    """Redact UUIDs and MAC addresses from arbitrary text."""
    text = UUID_REGEX.sub("[REDACTED-UUID]", text)
    text = MAC_ADDRESS_REGEX.sub("xx:xx:xx:xx:xx:xx", text)
    return text


def _sanitize_data_structure(data: Any) -> Any:
    """Recursively redact sensitive data from nested structures."""
    if isinstance(data, dict):
        sanitized: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(term in k_lower for term in ("serial", "uuid", "mac_address", "macaddress", "chassis_serial")):
                if isinstance(v, str):
                    if "uuid" in k_lower:
                        sanitized[k] = "[REDACTED-UUID]"
                    elif "mac" in k_lower:
                        sanitized[k] = "xx:xx:xx:xx:xx:xx"
                    else:
                        sanitized[k] = "[REDACTED-SERIAL]"
                else:
                    sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize_data_structure(v)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_data_structure(item) for item in data]
    elif isinstance(data, str):
        return _sanitize_string(data)
    else:
        return data


def sanitize_hardware_snapshot(snapshot: HardwareSnapshot) -> HardwareSnapshot:
    """Produce a privacy-sanitized copy of a HardwareSnapshot suitable for test fixtures and reports."""
    # Convert to dict, sanitize fields and raw evidence, then reconstitute
    raw_dict = snapshot.to_dict()

    # Redact top-level sensitive fields
    if raw_dict.get("serial_number"):
        raw_dict["serial_number"] = "[REDACTED-SERIAL]"
    if raw_dict.get("uuid"):
        raw_dict["uuid"] = "[REDACTED-UUID]"

    # Redact network MAC addresses
    for section in ("ethernet", "wifi", "bluetooth"):
        for device in raw_dict.get(section, []):
            if device.get("mac_address"):
                device["mac_address"] = "xx:xx:xx:xx:xx:xx"

    # Redact storage serial numbers
    for storage in raw_dict.get("storage", []):
        if storage.get("serial"):
            storage["serial"] = "[REDACTED-STORAGE-SERIAL]"

    # Redact raw evidence recursively
    if "raw_evidence" in raw_dict and isinstance(raw_dict["raw_evidence"], dict):
        raw_dict["raw_evidence"] = _sanitize_data_structure(raw_dict["raw_evidence"])

    return HardwareSnapshot.from_dict(raw_dict)
