"""Bounded EDID parser: report panel model/native timing, never panel serial."""
from typing import Optional


def edid_panel(data: bytes) -> Optional[tuple[str, str]]:
    if len(data) < 128 or len(data) > 4096 or data[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    if sum(data[:128]) % 256:
        return None
    timing = data[54:72]
    if timing[:2] == b"\x00\x00":
        return None
    horizontal = timing[2] + ((timing[4] >> 4) << 8)
    vertical = timing[5] + ((timing[7] >> 4) << 8)
    if not horizontal or not vertical:
        return None
    name = "Internal panel"
    for offset in (54, 72, 90, 108):
        descriptor = data[offset:offset + 18]
        if descriptor[:5] == b"\x00\x00\x00\xfc\x00":
            name = descriptor[5:].decode("ascii", errors="replace").strip("\x00\n ")
    return name, f"{horizontal}x{vertical}"
