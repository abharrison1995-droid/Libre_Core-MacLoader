"""Hardware detection providers and processing utilities."""

import platform
from typing import Optional

from macloader.detection.base import BaseHardwareProvider
from macloader.detection.fixture import FixtureHardwareProvider
from macloader.detection.linux import LinuxHardwareProvider
from macloader.detection.windows import WindowsHardwareProvider
from macloader.detection.normalize import (
    normalize_hex_id,
    normalize_dmi_string,
    extract_machine_type,
)
from macloader.detection.sanitize import sanitize_hardware_snapshot


def get_default_provider() -> BaseHardwareProvider:
    """Return the appropriate native hardware detection provider for the host OS."""
    sys_name = platform.system().lower()
    if sys_name == "linux":
        return LinuxHardwareProvider()
    elif sys_name == "windows":
        return WindowsHardwareProvider()
    else:
        # Fallback to linux provider (e.g. POSIX)
        return LinuxHardwareProvider()


__all__ = [
    "BaseHardwareProvider",
    "LinuxHardwareProvider",
    "WindowsHardwareProvider",
    "FixtureHardwareProvider",
    "normalize_hex_id",
    "normalize_dmi_string",
    "extract_machine_type",
    "sanitize_hardware_snapshot",
    "get_default_provider",
]
