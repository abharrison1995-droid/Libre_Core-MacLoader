"""Archive inspection and safe extraction utilities with path traversal protection."""

import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import List, Optional
import zipfile

from macloader.exceptions import ArchiveSecurityError

logger = logging.getLogger(__name__)


def validate_zip_archive(zip_path: Path, max_members: int = 10000, max_expanded_bytes: int = 2 * 1024 * 1024 * 1024) -> List[str]:
    """Inspect a zip file and verify it contains no directory traversal or absolute path exploits."""
    if not zip_path.is_file():
        raise ArchiveSecurityError(f"Archive file does not exist: {zip_path}")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member_names = zf.namelist()
            if len(member_names) > max_members:
                raise ArchiveSecurityError(f"Archive contains too many members ({len(member_names)} > {max_members})")
            seen = set()
            expanded = 0
            for name in member_names:
                normalized_name = name.replace("\\", "/")
                folded = normalized_name.rstrip("/").casefold()
                if folded and folded in seen:
                    raise ArchiveSecurityError(f"Duplicate or colliding archive member: {name}")
                if folded:
                    seen.add(folded)
                # Disallow absolute paths
                if normalized_name.startswith("/") or normalized_name.startswith("\\") or (len(normalized_name) >= 2 and normalized_name[1] == ":"):
                    raise ArchiveSecurityError(f"Insecure absolute path in archive '{zip_path.name}': {name}")

                # Disallow path traversal components
                parts = Path(normalized_name).parts
                if ".." in parts:
                    raise ArchiveSecurityError(f"Insecure directory traversal path in archive '{zip_path.name}': {name}")
                for part in parts:
                    basename = part.split(".", 1)[0].rstrip(" .").upper()
                    if part.endswith((".", " ")) or ":" in part or basename in {"CON", "PRN", "AUX", "NUL"} or basename.startswith(("COM", "LPT")) and basename[3:].isdigit():
                        raise ArchiveSecurityError(f"Insecure Windows path in archive '{zip_path.name}': {name}")
                info = zf.getinfo(name)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ArchiveSecurityError(f"Symlink member is not allowed in archive '{zip_path.name}': {name}")
                expanded += info.file_size
                if expanded > max_expanded_bytes:
                    raise ArchiveSecurityError("Archive expanded size exceeds the configured limit")

            return member_names
    except zipfile.BadZipFile as e:
        raise ArchiveSecurityError(f"Corrupt or invalid zip archive '{zip_path.name}': {e}") from e


def safe_extract_zip(
    zip_path: Path,
    target_dir: Path,
    members: Optional[List[str]] = None,
) -> None:
    """Safely extract members from a zip archive into target_dir preventing path escape."""
    if zip_path.is_symlink():
        raise ArchiveSecurityError(f"Archive must not be a symlink: {zip_path}")
    if target_dir.exists() and target_dir.is_symlink():
        raise ArchiveSecurityError(f"Extraction target must not be a symlink: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_target = target_dir.resolve()

    # Bind validation and extraction to a private copy so a replacement of the
    # caller-supplied archive cannot change the bytes between the two phases.
    with tempfile.TemporaryDirectory(prefix="macloader-archive-") as temp_name:
        snapshot = Path(temp_name) / "archive.zip"
        try:
            shutil.copyfile(zip_path, snapshot, follow_symlinks=False)
            available = validate_zip_archive(snapshot)
            with zipfile.ZipFile(snapshot, "r") as zf:
                namelist = members if members is not None else zf.namelist()
                for member in namelist:
                    if member not in available:
                        raise ArchiveSecurityError(f"Requested archive member is absent: {member}")
                    dest_path = (target_dir / member).resolve()
                    if not dest_path.is_relative_to(resolved_target):
                        raise ArchiveSecurityError(
                            f"Extraction path escape attempt: member '{member}' resolves to '{dest_path}' outside '{target_dir}'"
                        )
                    if dest_path.exists() and dest_path.is_symlink():
                        raise ArchiveSecurityError(f"Refusing to overwrite symlink during extraction: {dest_path}")
                    current = target_dir
                    relative_parts = Path(member.replace("\\", "/")).parts[:-1]
                    for part in relative_parts:
                        current = current / part
                        if current.exists() and current.is_symlink():
                            raise ArchiveSecurityError(f"Refusing to follow symlink during extraction: {current}")
                    info = zf.getinfo(member)
                    if member.endswith("/"):
                        dest_path.mkdir(parents=True, exist_ok=True)
                        continue
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, dest_path.open("xb") as dst:
                        written = 0
                        while chunk := src.read(65536):
                            written += len(chunk)
                            if written > 2 * 1024 * 1024 * 1024:
                                raise ArchiveSecurityError("Archive expanded size exceeds the configured limit")
                            dst.write(chunk)
        except OSError as exc:
            raise ArchiveSecurityError(f"Unable to snapshot or extract archive '{zip_path.name}': {exc}") from exc
