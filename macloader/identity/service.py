"""Private SMBIOS identity generation, storage, reuse, and redaction."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import uuid
from typing import Dict, Optional

from macloader.domain.contracts import IdentityReference, canonical_json_digest


class IdentityServiceError(ValueError):
    """Raised when private identity handling cannot safely continue."""


REQUIRED_FIELDS = ("SystemProductName", "SystemSerialNumber", "MLB", "SystemUUID", "ROM")
ALLOWED_PRODUCT = "MacBookPro15,2"


@dataclass(frozen=True)
class PrivateIdentity:
    values: Dict[str, str]
    storage_ref: str
    digest: str


class IdentityService:
    """Keep identity values private and expose only opaque references."""

    def __init__(self, store_dir: Path, identity_tool: Optional[Path] = None) -> None:
        # Keep the lexical path so symlinked roots/ancestors can be rejected;
        # resolving here would silently turn an unsafe destination into an
        # apparently safe one.
        self.store_dir = Path(store_dir).expanduser().absolute()
        self.identity_tool = identity_tool.absolute() if identity_tool else None

    def _assert_private_root(self) -> None:
        for ancestor in (self.store_dir, *self.store_dir.parents):
            if ancestor.exists() and ancestor.is_symlink():
                raise IdentityServiceError("Private identity storage path contains a symlink")
        if self.store_dir.exists() and not self.store_dir.is_dir():
            raise IdentityServiceError("Private identity storage root is not a directory")
        self.store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.store_dir.is_symlink():
            raise IdentityServiceError("Private identity storage root must not be a symlink")
        if os.name != "nt" and self.store_dir.stat().st_mode & 0o077:
            try:
                self.store_dir.chmod(0o700)
            except OSError as exc:
                raise IdentityServiceError(f"Unable to protect private identity directory: {exc}") from exc
            if self.store_dir.stat().st_mode & 0o077:
                raise IdentityServiceError("Private identity storage directory permissions are too broad")
        if os.name == "nt":
            account = getpass.getuser()
            try:
                result = subprocess.run(
                    [
                        "icacls", str(self.store_dir), "/inheritance:r", "/grant:r",
                        f"{account}:(OI)(CI)F",
                    ],
                    capture_output=True, text=True, check=False,
                )
            except OSError as exc:
                raise IdentityServiceError("Unable to protect private identity Windows ACL") from exc
            if result.returncode != 0:
                raise IdentityServiceError("Unable to protect private identity Windows ACL")
            self._assert_private_acl(self.store_dir)

    @staticmethod
    def _assert_private_acl(path: Path) -> None:
        try:
            result = subprocess.run(
                ["icacls", str(path)], capture_output=True, text=True, check=False,
            )
        except OSError as exc:
            raise IdentityServiceError("Unable to inspect private identity Windows ACL") from exc
        if result.returncode != 0:
            raise IdentityServiceError("Unable to inspect private identity Windows ACL")
        acl = result.stdout.casefold()
        if "(i)" in acl or any(
            principal in acl
            for principal in ("everyone:", "users:", "authenticated users:")
        ):
            raise IdentityServiceError("Private identity Windows ACL is too broad or inherited")

    @staticmethod
    def _assert_private_file(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise IdentityServiceError("Private identity reference is missing or unsafe")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise IdentityServiceError("Private identity permissions are too broad")
        if os.name == "nt":
            IdentityService._assert_private_acl(path)

    @staticmethod
    def _read_private_values(path: Path) -> Dict[str, str]:
        """Read through a regular no-follow descriptor after boundary checks."""
        fd: Optional[int] = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            status = os.fstat(fd)
            if not stat.S_ISREG(status.st_mode) or status.st_size > 64 * 1024:
                raise IdentityServiceError("Private identity reference is missing or unsafe")
            data = json.loads(os.read(fd, status.st_size).decode("utf-8"))
        except IdentityServiceError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise IdentityServiceError(f"Private identity cannot be read: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise IdentityServiceError("Private identity has an invalid shape")
        return data

    @staticmethod
    def validate(values: Dict[str, str]) -> None:
        if set(values) != set(REQUIRED_FIELDS):
            raise IdentityServiceError("SMBIOS identity has missing or unsupported fields")
        if values["SystemProductName"] != ALLOWED_PRODUCT:
            raise IdentityServiceError("SMBIOS model is not permitted by the reviewed T480s policy")
        for key in ("SystemSerialNumber", "MLB"):
            value = values[key]
            if not isinstance(value, str) or not value or not value.isalnum() or not 4 <= len(value) <= 32:
                raise IdentityServiceError(f"SMBIOS {key} has an invalid format")
        uuid_value = values["SystemUUID"]
        try:
            uuid.UUID(uuid_value)
        except (ValueError, AttributeError):
            raise IdentityServiceError("SMBIOS SystemUUID must be a UUID")
        rom = values["ROM"]
        if len(rom) != 12 or any(char not in "0123456789abcdefABCDEF" for char in rom):
            raise IdentityServiceError("SMBIOS ROM must be six bytes represented as hexadecimal")

    @staticmethod
    def fake_identity() -> Dict[str, str]:
        """Return a deterministic test identity; never call the real generator."""
        return {
            "SystemProductName": ALLOWED_PRODUCT,
            "SystemSerialNumber": "P4TESTSERIAL01",
            "MLB": "P4TESTMLB000000001",
            "SystemUUID": "00000000-0000-4000-8000-000000000004",
            "ROM": "001122334455",
        }

    def generate(self, *, allow_real: bool = False) -> Dict[str, str]:
        """Generate through the pinned macserial tool only when explicitly enabled.

        Automated builds and tests must pass a fake identity or reuse a stored
        private identity.  This method is intentionally never called by the
        builder implicitly.
        """
        if not allow_real:
            raise IdentityServiceError("Real SMBIOS identity generation requires an explicit private-workflow choice")
        if self.identity_tool is None or self.identity_tool.is_symlink() or not self.identity_tool.is_file():
            raise IdentityServiceError("Trusted macserial is unavailable")
        try:
            completed = subprocess.run(
                [str(self.identity_tool), "--generate", "-m", ALLOWED_PRODUCT],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IdentityServiceError(f"Private identity tool failed: {exc}") from exc
        if completed.returncode != 0:
            raise IdentityServiceError("Private identity tool returned a failure status")
        first = next((line.strip() for line in completed.stdout.splitlines() if "|" in line), "")
        parts = [part.strip() for part in first.split("|", 1)]
        if len(parts) != 2:
            raise IdentityServiceError("Private identity tool returned no usable identity pair")
        values = {
            "SystemProductName": ALLOWED_PRODUCT,
            "SystemSerialNumber": parts[0],
            "MLB": parts[1],
            "SystemUUID": str(uuid.uuid4()),
            "ROM": secrets.token_hex(6),
        }
        self.validate(values)
        return values

    def store(
        self,
        values: Dict[str, str],
        *,
        storage_ref: Optional[str] = None,
        validate_values: bool = True,
    ) -> PrivateIdentity:
        if validate_values:
            self.validate(values)
        ref = storage_ref or f"{uuid.uuid4().hex}.json"
        if Path(ref).name != ref or not ref.endswith(".json"):
            raise IdentityServiceError("Private identity storage reference must be a simple JSON filename")
        self._assert_private_root()
        path = self.store_dir / ref
        if path.is_symlink():
            raise IdentityServiceError("Private identity path must not be a symlink")
        temporary: Optional[Path] = None
        try:
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=self.store_dir)
            temporary = Path(name)
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(values, sort_keys=True, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "nt":
                account = getpass.getuser()
                result = subprocess.run(
                    ["icacls", str(temporary), "/inheritance:r", "/grant:r", f"{account}:(R,W)"],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode != 0:
                    raise IdentityServiceError("Unable to apply private Windows ACL before publication")
            self._assert_private_root()
            if path.is_symlink():
                raise IdentityServiceError("Private identity path became a symlink")
            os.replace(temporary, path)
            temporary = None
        except OSError as exc:
            raise IdentityServiceError(f"Unable to protect private identity storage: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._assert_private_file(path)
        return PrivateIdentity(dict(values), ref, canonical_json_digest(values))

    def reuse(self, reference: IdentityReference) -> PrivateIdentity:
        if not reference.redacted or Path(reference.storage_ref).name != reference.storage_ref:
            raise IdentityServiceError("Identity reference is not a redacted local filename")
        path = self.store_dir / reference.storage_ref
        self._assert_private_root()
        if path.is_symlink() or not path.is_file():
            raise IdentityServiceError("Private identity reference is missing or unsafe")
        data = self._read_private_values(path)
        self.validate(data)
        self._assert_private_file(path)
        return PrivateIdentity(data, reference.storage_ref, canonical_json_digest(data))

    def reference_for(self, private: PrivateIdentity) -> IdentityReference:
        return IdentityReference("0.1", private.storage_ref, redacted=True)

    @staticmethod
    def redact(text: str, values: Optional[Dict[str, str]]) -> str:
        redacted = text
        for value in (values or {}).values():
            if not value:
                continue
            variants = {
                value,
                value.casefold(),
                base64.b64encode(value.encode("utf-8")).decode("ascii"),
                base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii"),
            }
            for variant in variants:
                redacted = redacted.replace(variant, "<redacted>")
        return redacted
