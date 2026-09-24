"""Read-only Linux capture of the reference T480s's firmware ACPI tables.

The Linux kernel exposes the firmware's static ACPI tables under
``/sys/firmware/acpi/tables``.  Reading them is non-destructive, but the files
are root-only, so the capture must run with ``sudo`` on the laptop itself.  The
capture:

* checks the DMI vendor, machine type and BIOS before reading any table;
* reads the DSDT and every statically installed SSDT, bounded and without
  following symlinks (runtime-loaded ``dynamic/`` tables are excluded);
* validates every table's signature, declared length and checksum;
* names the SSDTs in firmware order using the importer's convention
  (``ssdt.dat``, ``ssdt1.dat`` ...), refusing any count the importer would
  reject, and writes them owner-only into a new directory outside Git.

The output is private machine evidence.  It is imported with
``macloader evidence acpi-import`` and must never be published.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Optional

from macloader.build.acpi import MAX_ACPI_TABLE_BYTES, TABLE_NAMES, AcpiProcessor, normalize_bios_binding
from macloader.exceptions import BuildPlanError


SYSFS_ACPI_TABLES = Path("/sys/firmware/acpi/tables")
SYSFS_DMI = Path("/sys/class/dmi/id")
REFERENCE_MACHINE_TYPE = "20L8"
REFERENCE_BIOS_BINDING = "N22ET85W-1.62"
CAPTURE_SCHEMA = "1"
_SSDT_RE = re.compile(r"SSDT(\d*)\Z")


class AcpiCaptureError(ValueError):
    """The ACPI capture was refused; nothing usable was written."""


@dataclass(frozen=True)
class CapturedTable:
    file_name: str
    firmware_name: str
    length: int
    sha256: str


@dataclass(frozen=True)
class AcpiCaptureResult:
    capture_root: Path
    machine_type: str
    bios_binding: str
    tables: tuple[CapturedTable, ...]

    def summary(self) -> dict[str, Any]:
        """A redaction-safe summary: counts and names, never table contents."""
        return {
            "status": "captured_private",
            "machine_type": self.machine_type,
            "bios_binding": self.bios_binding,
            "table_count": len(self.tables),
            "dsdt_count": sum(item.file_name.startswith("dsdt") for item in self.tables),
            "ssdt_count": sum(item.file_name.startswith("ssdt") for item in self.tables),
            "files": [item.file_name for item in self.tables],
            "next_step": f"macloader evidence acpi-import CONFIG_ID {self.capture_root}",
        }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError as exc:
        raise AcpiCaptureError(f"DMI field {path.name} is unavailable on this host") from exc


def check_reference_machine(dmi_root: Path = SYSFS_DMI) -> tuple[str, str]:
    """Return (machine type, BIOS binding) only for the reference T480s."""
    vendor = _read_text(dmi_root / "sys_vendor")
    product = _read_text(dmi_root / "product_name")
    version = _read_text(dmi_root / "product_version")
    bios = _read_text(dmi_root / "bios_version")
    machine_type = product[:4].upper()
    if vendor.upper() != "LENOVO" or machine_type != REFERENCE_MACHINE_TYPE:
        raise AcpiCaptureError(
            f"This host is not a Lenovo {REFERENCE_MACHINE_TYPE} (found {vendor or 'unknown'} {machine_type or 'unknown'}); "
            "capture ACPI only on the reference ThinkPad T480s"
        )
    if "T480S" not in version.upper().replace(" ", ""):
        raise AcpiCaptureError("DMI product version does not identify a ThinkPad T480s")
    binding = normalize_bios_binding(bios)
    if binding != REFERENCE_BIOS_BINDING:
        raise AcpiCaptureError(
            f"BIOS {bios or 'unknown'} is not the reviewed N22ET85W (1.62); do not change BIOS for this capture"
        )
    return machine_type, binding


def _read_table(path: Path) -> bytes:
    try:
        # O_NONBLOCK keeps a FIFO or device node from stalling the capture;
        # it has no effect on the regular sysfs table attributes.
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except PermissionError as exc:
        raise AcpiCaptureError(
            "Firmware ACPI tables are readable only by root; rerun the capture with sudo on the T480s"
        ) from exc
    except OSError as exc:
        raise AcpiCaptureError(f"Firmware table {path.name} cannot be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AcpiCaptureError(f"Firmware table {path.name} is not a regular file")
        # sysfs reports st_size 0 for these attributes; read until EOF, bounded.
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ACPI_TABLE_BYTES:
                raise AcpiCaptureError(f"Firmware table {path.name} exceeds the safe size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _firmware_tables(tables_root: Path) -> list[tuple[str, Path]]:
    if tables_root.is_symlink() or not tables_root.is_dir():
        raise AcpiCaptureError("This host does not expose firmware ACPI tables (Linux sysfs is required)")
    dsdt: list[tuple[str, Path]] = []
    ssdts: list[tuple[int, str, Path]] = []
    for entry in tables_root.iterdir():
        if entry.name == "DSDT":
            dsdt.append((entry.name, entry))
            continue
        match = _SSDT_RE.fullmatch(entry.name)
        if match:
            ssdts.append((int(match.group(1) or 0), entry.name, entry))
    if len(dsdt) != 1:
        raise AcpiCaptureError("Firmware does not expose exactly one DSDT")
    ordered = [name_path for name_path in dsdt]
    ordered.extend((name, path) for _index, name, path in sorted(ssdts))
    return ordered


def _inside_git_checkout(path: Path) -> bool:
    return any((ancestor / ".git").exists() for ancestor in (path, *path.parents))


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def capture_acpi_tables(
    destination: Path,
    *,
    tables_root: Path = SYSFS_ACPI_TABLES,
    dmi_root: Path = SYSFS_DMI,
    owner: Optional[tuple[int, int]] = None,
    now: Optional[datetime] = None,
) -> AcpiCaptureResult:
    """Capture the reference machine's DSDT and SSDTs into a new private directory."""
    if os.name == "nt":
        raise AcpiCaptureError("Linux ACPI capture is unavailable on Windows; use tools/capture_t480s_followup.ps1")
    destination = Path(destination).expanduser().absolute()
    for ancestor in (destination, *destination.parents):
        if ancestor.is_symlink():
            raise AcpiCaptureError("Capture destination path contains a symlink")
    if destination.exists():
        raise AcpiCaptureError("Capture destination already exists; choose a new private directory")
    if _inside_git_checkout(destination):
        raise AcpiCaptureError("Capture destination is inside a Git checkout; private ACPI must stay out of the repository")
    machine_type, binding = check_reference_machine(dmi_root)

    raw: list[tuple[str, bytes]] = []
    for firmware_name, path in _firmware_tables(tables_root):
        data = _read_table(path)
        try:
            metadata = AcpiProcessor._validate_table_bytes(data, firmware_name)
        except BuildPlanError as exc:
            raise AcpiCaptureError(str(exc)) from exc
        expected_signature = "DSDT" if firmware_name == "DSDT" else "SSDT"
        if metadata["signature"] != expected_signature:
            raise AcpiCaptureError(f"Firmware table {firmware_name} has an unexpected signature")
        raw.append((firmware_name, data))
    ssdt_count = len(raw) - 1
    expected_ssdts = len(TABLE_NAMES) - 1
    if ssdt_count != expected_ssdts:
        raise AcpiCaptureError(
            f"Firmware exposes {ssdt_count} static SSDTs, but the reviewed T480s profile requires exactly "
            f"{expected_ssdts}; stop and review the profile instead of renaming or dropping tables"
        )

    table_dir = destination / "PRIVATE-ACPI"
    destination.mkdir(mode=0o700, parents=False)
    try:
        table_dir.mkdir(mode=0o700)
        captured: list[CapturedTable] = []
        for file_name, (firmware_name, data) in zip(TABLE_NAMES, raw):
            _write_private(table_dir / file_name, data)
            captured.append(CapturedTable(file_name, firmware_name, len(data), hashlib.sha256(data).hexdigest()))
        sums = "".join(f"{item.sha256}  {item.file_name}\n" for item in captured).encode("ascii")
        _write_private(table_dir / "SHA256SUMS", sums)
        manifest: Mapping[str, Any] = {
            "schema_version": CAPTURE_SCHEMA,
            "method": "linux-sysfs-firmware-acpi-tables",
            "captured_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "machine_type": machine_type,
            "bios_binding": binding,
            "tables": [item.__dict__ for item in captured],
        }
        _write_private(destination / "capture-manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        # Prove that the importer will accept exactly this layout.
        for path in AcpiProcessor._find_tables(table_dir):
            AcpiProcessor._validate_table(path)
        if owner is not None:
            # Hand children over first and the top directory last, so the
            # invoking user cannot swap a path component while root works.
            for path in (*table_dir.iterdir(), destination / "capture-manifest.json", table_dir, destination):
                os.chown(path, owner[0], owner[1], follow_symlinks=False)
    except BaseException:
        for child in sorted(destination.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
        destination.rmdir()
        raise
    return AcpiCaptureResult(destination, machine_type, binding, tuple(captured))


def sudo_owner(environ: Mapping[str, str] = os.environ) -> Optional[tuple[int, int]]:
    """Return the invoking user's ids when running under sudo as root."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    uid, gid = environ.get("SUDO_UID", ""), environ.get("SUDO_GID", "")
    if uid.isdigit() and gid.isdigit() and int(uid) != 0:
        return int(uid), int(gid)
    return None
