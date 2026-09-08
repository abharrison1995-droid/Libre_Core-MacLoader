"""Normalization utilities for hardware identifiers, DMI strings, and PCI properties."""

import re
from typing import Optional


def normalize_hex_id(value: Optional[str], length: int = 4) -> Optional[str]:
    """Normalize a hexadecimal ID (e.g. '0x8086', '8086', ' 8086 ') to lowercase padded hex."""
    if value is None:
        return None
    val = value.strip()
    if not val:
        return None
    if val.lower().startswith("0x"):
        val = val[2:]
    val = val.strip().lower()
    # Check if all characters are valid hex
    if not re.fullmatch(r"[0-9a-f]+", val):
        return None
    if len(val) < length:
        val = val.zfill(length)
    elif len(val) > length:
        val = val[:length]
    return val


def normalize_dmi_string(value: Optional[str]) -> str:
    """Normalize a DMI string by trimming, removing non-printable/null characters."""
    if value is None:
        return ""
    val = value.strip()
    val = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", val)
    return val.strip()


def extract_machine_type(product_version: Optional[str], product_name: Optional[str]) -> Optional[str]:
    """Extract Lenovo 4-character machine type code (e.g. '20L7', '20L8', '20L5', '20L6').

    Lenovo DMI fields often look like:
    - product_name: '20L7CTO1WW' -> machine_type is '20L7'
    - product_version: 'ThinkPad T480s' or '20L7001YUS' -> '20L7'
    """
    candidates = [product_name, product_version]
    for candidate in candidates:
        if not candidate:
            continue
        cleaned = candidate.strip().upper()
        # Modern ThinkPad machine types begin with '20' + 2 alphanumeric chars (e.g. 20L7CTO1WW, 20L8, 20L5, 20KH)
        match_20 = re.search(r"\b(20[A-Z0-9]{2})\b", cleaned)
        if match_20:
            return match_20.group(1)
        match_20_start = re.match(r"^(20[A-Z0-9]{2})", cleaned)
        if match_20_start:
            return match_20_start.group(1)

        # Older classic ThinkPads use 4 digits starting with 2, 3, 4, or 7
        match_classic = re.search(r"\b([2347]\d{3})[A-Z0-9]*\b", cleaned)
        if match_classic and ("THINKPAD" in cleaned or len(cleaned) <= 10):
            return match_classic.group(1)

    return None


def infer_cpu_generation(model_name: Optional[str]) -> Optional[str]:
    """Infer Intel CPU microarchitecture generation consistently across host platforms.

    Supports Intel mobile Core architectures found in ThinkPad models:
    - 8th Gen Quad-Core (Kaby Lake Refresh): 8250U, 8350U, 8550U, 8650U, 8th Gen
    - 7th Gen Dual-Core (Kaby Lake): 7200U, 7300U, 7500U, 7600U, 7th Gen
    - 6th Gen Dual-Core (Skylake): 6200U, 6300U, 6500U, 6600U, 6th Gen
    - 10th Gen (Comet Lake): 10210U, 10510U, 10710U
    - 10th Gen (Ice Lake): 1065G7, Ice Lake
    """
    if not isinstance(model_name, str) or not model_name.strip():
        return None
    name = model_name.upper()

    # 8th Gen (Kaby Lake Refresh)
    if any(token in name for token in ("8250U", "8350U", "8550U", "8650U")):
        return "Kaby Lake Refresh"
    if "8TH GEN" in name:
        return "Kaby Lake Refresh"
    if re.search(r"\bI[357]-8\d{3}U\b", name):
        return "Kaby Lake Refresh"

    # 7th Gen (Kaby Lake)
    if any(token in name for token in ("7200U", "7300U", "7500U", "7600U")):
        return "Kaby Lake"
    if "7TH GEN" in name:
        return "Kaby Lake"
    if re.search(r"\bI[357]-7\d{3}U\b", name):
        return "Kaby Lake"

    # 6th Gen (Skylake)
    if any(token in name for token in ("6200U", "6300U", "6500U", "6600U")):
        return "Skylake"
    if "6TH GEN" in name:
        return "Skylake"

    # 10th Gen (Comet Lake / Ice Lake)
    if any(token in name for token in ("10210U", "10510U", "10710U")):
        return "Comet Lake"
    if "1065G7" in name or "ICE LAKE" in name:
        return "Ice Lake"

    return None
