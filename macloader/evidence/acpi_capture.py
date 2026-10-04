"""Read-only firmware capture. No AML execution and no caller-selected root paths."""
from dataclasses import dataclass
import base64
import json
import shutil
import time
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Protocol

from macloader.exceptions import BuildPlanError
from macloader.build.acpi import AcpiProcessor, MAX_ACPI_TABLE_BYTES, TABLE_NAMES


class CaptureError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AcpiCaptureProvider(Protocol):
    def capture(self, cancel: Callable[[], bool]) -> dict[str, bytes]: ...


def validate_capture(tables: dict[str, bytes]) -> dict[str, bytes]:
    if set(tables) != set(TABLE_NAMES):
        raise CaptureError("ACPI_TABLE_SET", "Firmware capture must contain one DSDT and eleven SSDTs.")
    for name, data in tables.items():
        try:
            metadata = AcpiProcessor._validate_table_bytes(data, name)
        except BuildPlanError as exc:
            raise CaptureError("ACPI_TABLE_INVALID", "Firmware table header, length or checksum is invalid.") from exc
        if metadata["signature"] != ("DSDT" if name == "dsdt.dat" else "SSDT"):
            raise CaptureError("ACPI_SIGNATURE", "A firmware table has the wrong signature.")
    return tables


@dataclass
class LinuxAcpiCaptureProvider:
    # Injectable root is a test seam. Production construction always uses sysfs.
    root: Path = Path("/sys/firmware/acpi/tables")

    def _read(self, cancel: Callable[[], bool]) -> dict[str, bytes]:
        if cancel():
            raise CaptureError("CANCELLED", "Firmware capture paused.")
        try:
            paths = sorted((p for p in self.root.iterdir() if re.fullmatch(r"DSDT|SSDT\d*", p.name)), key=lambda p: (p.name[:4], int(p.name[4:] or "0")))
            if len(paths) != len(TABLE_NAMES) or sum(p.name == "DSDT" for p in paths) != 1:
                raise CaptureError("ACPI_TABLE_SET", "The firmware table set differs from the reviewed campaign.")
            tables = {}
            for name, path in zip(TABLE_NAMES, paths):
                if cancel():
                    raise CaptureError("CANCELLED", "Firmware capture paused.")
                if path.is_symlink():
                    raise CaptureError("ACPI_UNSAFE_SOURCE", "Firmware source contains an unsafe link.")
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as handle:
                    data = handle.read(MAX_ACPI_TABLE_BYTES + 1)
                if len(data) > MAX_ACPI_TABLE_BYTES:
                    raise CaptureError("ACPI_SIZE", "Firmware table exceeds the capture limit.")
                tables[name] = data
            return validate_capture(tables)
        except PermissionError as exc:
            raise CaptureError("ACPI_PERMISSION", "The operating system must grant read access to firmware tables. No firmware was changed.") from exc
        except OSError as exc:
            raise CaptureError("ACPI_UNAVAILABLE", "Firmware tables could not be read on this host.") from exc

    def capture(self, cancel: Callable[[], bool] = lambda: False) -> dict[str, bytes]:
        before = self._read(cancel)
        if before != self._read(cancel):
            raise CaptureError("ACPI_CHANGED", "Firmware tables changed during capture; nothing was accepted.")
        return before


# Privileged code is fixed, isolated standard-library code. It reads only kernel
# table files and returns bytes through a private pipe; it cannot write files,
# load AML, import project modules, or accept caller paths.
LINUX_READ_HELPER = r"""
import base64, json, os, pathlib, re
root = pathlib.Path('/sys/firmware/acpi/tables')
def read():
    paths = sorted(p for p in root.iterdir() if re.fullmatch(r'DSDT|SSDT\d*', p.name))
    if len(paths) != 12: raise ValueError('Unexpected table count')
    result = {}
    total = 0
    for p in paths:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f: data = f.read(64*1024*1024+1)
        total += len(data)
        if total > 64*1024*1024: raise ValueError('Size limit')
        result[p.name] = base64.b64encode(data).decode('ascii')
    return result
first = read()
if first != read(): raise ValueError('Changed tables')
print(json.dumps(first))
"""


class LinuxElevatedAcpiCaptureProvider(LinuxAcpiCaptureProvider):
    def capture(self, cancel: Callable[[], bool] = lambda: False) -> dict[str, bytes]:
        try:
            return super().capture(cancel)
        except CaptureError as exc:
            if exc.code != "ACPI_PERMISSION":
                raise
        if self.root != Path("/sys/firmware/acpi/tables") or not shutil.which("pkexec") or not Path("/usr/bin/python3").is_file():
            raise CaptureError("ACPI_PERMISSION", "Allow firmware-table read access through the operating system; this host has no narrow capture helper available.")
        process = subprocess.Popen(["pkexec", "/usr/bin/python3", "-I", "-c", LINUX_READ_HELPER], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        started = time.monotonic()
        try:
            while True:
                if cancel() or time.monotonic() - started > 120:
                    AcpiProcessor._terminate_process(process)
                    raise CaptureError("CANCELLED", "Firmware capture paused; no firmware was changed.")
                try:
                    output, _ = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise CaptureError("ACPI_PERMISSION", "The operating system did not grant firmware-table read access.")
            raw = json.loads(output)
            paths = sorted(raw, key=lambda n: (n[:4], int(n[4:] or "0")))
            if len(paths) != len(TABLE_NAMES) or paths[0] != "DSDT" or not all(re.fullmatch(r"SSDT\d*", n) for n in paths[1:]):
                raise CaptureError("ACPI_TABLE_SET", "Firmware capture differs from the reviewed table set.")
            return validate_capture({name: base64.b64decode(raw[source], validate=True) for name, source in zip(TABLE_NAMES, paths)})
        finally:
            AcpiProcessor._terminate_process(process)


@dataclass
class WindowsAcpiCaptureProvider:
    tool: Path
    expected_sha256: str
    destination: Path

    def capture(self, cancel: Callable[[], bool] = lambda: False) -> dict[str, bytes]:
        if cancel():
            raise CaptureError("CANCELLED", "Firmware capture paused.")
        if self.tool.is_symlink() or not self.tool.is_file() or hashlib.sha256(self.tool.read_bytes()).hexdigest() != self.expected_sha256:
            raise CaptureError("ACPI_TOOL_UNTRUSTED", "The firmware capture tool is missing or fails its trusted hash check.")
        # Destination is a privately created empty staging directory owned by WorkflowService.
        if self.destination.is_symlink() or any(self.destination.iterdir()):
            raise CaptureError("ACPI_UNSAFE_DESTINATION", "Firmware capture requires an empty private workspace.")
        try:
            version = subprocess.run([str(self.tool), "-v"], capture_output=True, timeout=10, check=False)
            if b"20260408" not in version.stdout + version.stderr:
                raise CaptureError("ACPI_TOOL_VERSION", "The firmware capture tool has the wrong version.")
            completed = subprocess.run([str(self.tool), "-b"], cwd=self.destination, capture_output=True, timeout=60, check=False)
            if cancel():
                raise CaptureError("CANCELLED", "Firmware capture paused; partial tables were not accepted.")
            if completed.returncode:
                raise CaptureError("ACPI_PERMISSION", "Windows must grant the capture tool access to firmware tables.")
            paths = AcpiProcessor._find_tables(self.destination)
            first = {p.name.lower(): AcpiProcessor._read_table_bytes(p) for p in paths}
            if first != {p.name.lower(): AcpiProcessor._read_table_bytes(p) for p in paths}:
                raise CaptureError("ACPI_CHANGED", "Firmware files changed during capture.")
            return validate_capture(first)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CaptureError("ACPI_CAPTURE_FAILED", "Firmware capture could not complete. No firmware was changed.") from exc
