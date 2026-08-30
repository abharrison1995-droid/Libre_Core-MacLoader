"""Unit tests for safe zip archive inspection and path traversal protection."""

from pathlib import Path
import zipfile
import pytest

from macloader.dependencies.archive import safe_extract_zip, validate_zip_archive
from macloader.exceptions import ArchiveSecurityError


def test_validate_safe_zip_archive(tmp_path: Path) -> None:
    zip_file = tmp_path / "safe.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("Lilu.kext/Contents/Info.plist", "<plist/>")
        zf.writestr("Lilu.kext/Contents/MacOS/Lilu", "binary")

    members = validate_zip_archive(zip_file)
    assert len(members) == 2
    assert "Lilu.kext/Contents/Info.plist" in members

    extract_dir = tmp_path / "extracted"
    safe_extract_zip(zip_file, extract_dir)
    assert (extract_dir / "Lilu.kext" / "Contents" / "Info.plist").is_file()


def test_validate_rejects_path_traversal(tmp_path: Path) -> None:
    zip_file = tmp_path / "traversal.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("../escape.txt", "malicious payload")

    with pytest.raises(ArchiveSecurityError) as exc:
        validate_zip_archive(zip_file)
    assert "Insecure directory traversal path" in str(exc.value)


def test_validate_rejects_absolute_path(tmp_path: Path) -> None:
    zip_file = tmp_path / "absolute.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("/etc/malicious", "payload")

    with pytest.raises(ArchiveSecurityError) as exc:
        validate_zip_archive(zip_file)
    assert "Insecure absolute path" in str(exc.value)
