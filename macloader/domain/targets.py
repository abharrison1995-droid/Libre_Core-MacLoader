"""Exact operating-system target contracts.

Product names are useful for display, but an accepted configuration must bind
to an exact version and build from the trusted release catalog.
"""

from dataclasses import dataclass
import re
from typing import Any, Dict


_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
_BUILD_RE = re.compile(r"^[0-9]{2}[A-Z][0-9]{2,8}[a-z]?$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MacOsTarget:
    """A fully selected macOS release; aliases such as ``latest`` are invalid."""

    product_id: str
    product_name: str
    version: str
    build: str
    release_record_digest: str

    def __post_init__(self) -> None:
        if not self.product_id.strip() or not self.product_name.strip():
            raise ValueError("macOS target product identity is required")
        if not _VERSION_RE.fullmatch(self.version):
            raise ValueError("macOS target version must be an exact dotted version")
        if not _BUILD_RE.fullmatch(self.build):
            raise ValueError("macOS target build must be an exact Apple build identifier")
        if not _DIGEST_RE.fullmatch(self.release_record_digest.lower()):
            raise ValueError("macOS target release_record_digest must be a lowercase SHA-256 digest")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "version": self.version,
            "build": self.build,
            "release_record_digest": self.release_record_digest.lower(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MacOsTarget":
        if not isinstance(data, dict):
            raise ValueError("macOS target must be a mapping")
        required = {"product_id", "product_name", "version", "build", "release_record_digest"}
        if set(data) != required:
            raise ValueError("macOS target has missing or unknown fields")
        if not all(isinstance(data[key], str) for key in required):
            raise ValueError("macOS target fields must be strings")
        return cls(**{key: data[key] for key in required})
