"""Archive inspection and safe extraction utilities with path traversal protection."""

import logging
import os
from pathlib import Path
from typing import List, Optional
import zipfile

from macloader.exceptions import ArchiveSecurityError

logger = logging.getLogger(__name__)


def validate_zip_archive(zip_path: Path) -> List[str]:
    """Inspect a zip file and verify it contains no directory traversal or absolute path exploits."""
    if not zip_path.is_file():
        raise ArchiveSecurityError(f"Archive file does not exist: {zip_path}")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member_names = zf.namelist()
            for name in member_names:
                # Disallow absolute paths
                if name.startswith("/") or name.startswith("\\") or (len(name) >= 2 and name[1] == ":"):
                    raise ArchiveSecurityError(f"Insecure absolute path in archive '{zip_path.name}': {name}")

                # Disallow path traversal components
                parts = Path(name).parts
                if ".." in parts:
                    raise ArchiveSecurityError(f"Insecure directory traversal path in archive '{zip_path.name}': {name}")

            return member_names
    except zipfile.BadZipFile as e:
        raise ArchiveSecurityError(f"Corrupt or invalid zip archive '{zip_path.name}': {e}") from e


def safe_extract_zip(
    zip_path: Path,
    target_dir: Path,
    members: Optional[List[str]] = None,
) -> None:
    """Safely extract members from a zip archive into target_dir preventing path escape."""
    validate_zip_archive(zip_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_target = target_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = members if members is not None else zf.namelist()
        for member in namelist:
            # Check destination resolution
            dest_path = (target_dir / member).resolve()
            if not dest_path.is_relative_to(resolved_target):
                raise ArchiveSecurityError(
                    f"Extraction path escape attempt: member '{member}' resolves to '{dest_path}' outside '{target_dir}'"
                )
            zf.extract(member, path=str(target_dir))
