"""Private SMBIOS identity generation, storage, reuse, and redaction."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess
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
        self.store_dir = Path(store_dir).expanduser().resolve()
        self.identity_tool = identity_tool.resolve() if identity_tool else None

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

    def store(self, values: Dict[str, str], *, storage_ref: Optional[str] = None) -> PrivateIdentity:
        self.validate(values)
        ref = storage_ref or f"{canonical_json_digest(values)}.json"
        if Path(ref).name != ref or not ref.endswith(".json"):
            raise IdentityServiceError("Private identity storage reference must be a simple JSON filename")
        path = self.store_dir / ref
        if path.is_symlink():
            raise IdentityServiceError("Private identity path must not be a symlink")
        self.store_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, sort_keys=True, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError as exc:
            raise IdentityServiceError(f"Unable to protect private identity storage: {exc}") from exc
        if path.stat().st_mode & 0o077:
            raise IdentityServiceError("Private identity storage permissions are too broad")
        if path.is_file() and os.name == "nt":
            account = getpass.getuser()
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(R,W)"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                raise IdentityServiceError("Unable to apply private Windows ACL")
        return PrivateIdentity(dict(values), ref, canonical_json_digest(values))

    def reuse(self, reference: IdentityReference) -> PrivateIdentity:
        if not reference.redacted or Path(reference.storage_ref).name != reference.storage_ref:
            raise IdentityServiceError("Identity reference is not a redacted local filename")
        path = self.store_dir / reference.storage_ref
        if path.is_symlink() or not path.is_file():
            raise IdentityServiceError("Private identity reference is missing or unsafe")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IdentityServiceError(f"Private identity cannot be read: {exc}") from exc
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise IdentityServiceError("Private identity has an invalid shape")
        self.validate(data)
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
