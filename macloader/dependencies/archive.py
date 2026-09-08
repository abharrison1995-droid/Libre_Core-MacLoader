"""Archive inspection and safe extraction utilities with path traversal protection."""

import logging
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional
import zipfile

from macloader.exceptions import ArchiveSecurityError

logger = logging.getLogger(__name__)

DEFAULT_MAX_ARCHIVE_MEMBERS: int = 10000
DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES: int = 256 * 1024 * 1024  # 256 MiB per archive
DEFAULT_MAX_ARTIFACT_EXPANDED_BYTES: int = DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES
DEFAULT_MAX_BUILD_EXPANDED_BYTES: int = 512 * 1024 * 1024    # 512 MiB total build budget


def validate_zip_archive(
    zip_path: Path,
    max_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
    max_expanded_bytes: int = DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES,
) -> List[str]:
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
                if not normalized_name or normalized_name.strip("/") in {"", "."}:
                    raise ArchiveSecurityError(f"Invalid member name in archive '{zip_path.name}': {name}")
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
                    if (
                        part.endswith((".", " "))
                        or ":" in part
                        or basename in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
                        or (basename.startswith(("COM", "LPT")) and basename[3:].isdigit())
                    ):
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


def _extract_from_snapshot(
    snapshot: Path,
    target_dir: Path,
    members: Optional[List[str]] = None,
    member_map: Optional[Dict[str, Path]] = None,
    *,
    max_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
    max_expanded_bytes: int = DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES,
    cumulative_bytes_tracker: Optional[Callable[[int], None]] = None,
) -> int:
    """Internal bounded extraction engine operating on a verified snapshot."""
    resolved_target = target_dir.resolve()
    available = validate_zip_archive(snapshot, max_members=max_members, max_expanded_bytes=max_expanded_bytes)
    available_set = set(available)

    if member_map is not None:
        extraction_items: List[tuple[str, Path]] = list(member_map.items())
    else:
        namelist = members if members is not None else available
        extraction_items = [(m, target_dir / m) for m in namelist]

    # Pre-validate all requested members and destination boundaries before extracting
    for member, dest_path in extraction_items:
        if member not in available_set:
            raise ArchiveSecurityError(f"Requested archive member is absent: {member}")
        dest_resolved = dest_path.resolve()
        if not dest_resolved.is_relative_to(resolved_target):
            raise ArchiveSecurityError(
                f"Extraction path escape attempt: member '{member}' resolves to '{dest_resolved}' outside '{target_dir}'"
            )

    total_written = 0
    with zipfile.ZipFile(snapshot, "r") as zf:
        for member, dest_path in extraction_items:
            dest_resolved = dest_path.resolve()
            if dest_path.is_symlink():
                raise ArchiveSecurityError(f"Refusing to overwrite symlink during extraction: {dest_path}")

            try:
                rel_to_target = dest_resolved.relative_to(resolved_target)
            except ValueError:
                raise ArchiveSecurityError(f"Extraction path escape attempt: {dest_path}")

            current = resolved_target
            for part in rel_to_target.parts[:-1]:
                current = current / part
                if current.is_symlink():
                    raise ArchiveSecurityError(f"Refusing to follow symlink during extraction: {current}")

            info = zf.getinfo(member)
            is_directory = (
                member.endswith("/")
                or info.is_dir()
                or any(other.startswith(member + "/") for other in available_set)
            )
            if is_directory:
                try:
                    dest_path.mkdir(parents=True, exist_ok=True)
                except (FileExistsError, NotADirectoryError, PermissionError) as exc:
                    raise ArchiveSecurityError(f"Extraction collision: destination directory conflict: {dest_path}") from exc
                continue

            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
            except (FileExistsError, NotADirectoryError, PermissionError) as exc:
                raise ArchiveSecurityError(f"Extraction collision: destination parent conflict: {dest_path.parent}") from exc

            try:
                dst = dest_path.open("xb")
            except FileExistsError:
                raise ArchiveSecurityError(f"Extraction collision: destination file already exists: {dest_path}")
            except (IsADirectoryError, PermissionError) as exc:
                if dest_path.exists():
                    raise ArchiveSecurityError(f"Extraction collision: destination already exists: {dest_path}") from exc
                raise ArchiveSecurityError(f"Cannot create destination file '{dest_path}': {exc}") from exc

            try:
                with dst, zf.open(info, "r") as src:
                    while chunk := src.read(65536):
                        total_written += len(chunk)
                        if total_written > max_expanded_bytes:
                            raise ArchiveSecurityError(
                                f"Archive expanded size exceeds the configured limit of {max_expanded_bytes} bytes"
                            )
                        if cumulative_bytes_tracker is not None:
                            cumulative_bytes_tracker(len(chunk))
                        dst.write(chunk)
            except Exception:
                try:
                    dest_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    return total_written


def safe_extract_zip(
    zip_path: Path,
    target_dir: Path,
    members: Optional[List[str]] = None,
    member_map: Optional[Dict[str, Path]] = None,
    *,
    is_trusted_snapshot: bool = False,
    max_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
    max_expanded_bytes: int = DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES,
    cumulative_bytes_tracker: Optional[Callable[[int], None]] = None,
) -> int:
    """Safely extract members from a zip archive into target_dir preventing path escape.

    Args:
        zip_path: Path to the zip archive.
        target_dir: Destination directory. All extracted paths must resolve inside target_dir.
        members: Optional list of member names in the archive to extract. If omitted and
            member_map is None, extracts all members.
        member_map: Optional mapping of archive member name to destination Path. When provided,
            each member is extracted directly to its specified destination (which must resolve
            within target_dir).
        is_trusted_snapshot: When True, zip_path is already an exclusive private snapshot owned
            by the caller in a secure temporary directory, so redundant snapshot copying is skipped.
            When False, creates a private temporary copy to protect against TOCTOU mutation.
        max_members: Maximum number of members allowed in the zip archive.
        max_expanded_bytes: Maximum cumulative bytes allowed to be extracted from this archive.
        cumulative_bytes_tracker: Optional callback receiving chunk lengths as bytes are written,
            allowing callers to enforce a global build disk budget across multiple extractions.

    Returns:
        int: Total number of bytes written during extraction.
    """
    zip_path = Path(zip_path)
    target_dir = Path(target_dir)

    if not zip_path.is_file():
        raise ArchiveSecurityError(f"Archive file does not exist: {zip_path}")
    if zip_path.is_symlink():
        raise ArchiveSecurityError(f"Archive must not be a symlink: {zip_path}")
    if target_dir.is_symlink():
        raise ArchiveSecurityError(f"Extraction target must not be a symlink: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    if is_trusted_snapshot:
        return _extract_from_snapshot(
            zip_path,
            target_dir,
            members=members,
            member_map=member_map,
            max_members=max_members,
            max_expanded_bytes=max_expanded_bytes,
            cumulative_bytes_tracker=cumulative_bytes_tracker,
        )

    # Bind validation and extraction to a private copy so a replacement of the
    # caller-supplied archive cannot change the bytes between the two phases.
    with tempfile.TemporaryDirectory(prefix="macloader-archive-") as temp_name:
        snapshot = Path(temp_name) / "archive.zip"
        try:
            shutil.copyfile(zip_path, snapshot, follow_symlinks=False)
        except OSError as exc:
            raise ArchiveSecurityError(f"Unable to snapshot archive '{zip_path.name}': {exc}") from exc
        return _extract_from_snapshot(
            snapshot,
            target_dir,
            members=members,
            member_map=member_map,
            max_members=max_members,
            max_expanded_bytes=max_expanded_bytes,
            cumulative_bytes_tracker=cumulative_bytes_tracker,
        )
