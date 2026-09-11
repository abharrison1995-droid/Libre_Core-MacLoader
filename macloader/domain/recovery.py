"""Immutable Recovery discovery, lock and evidence contracts."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, Optional, Tuple

from macloader.domain.contracts import canonical_json_digest


class RecoveryState(str, Enum):
    UNKNOWN = "unknown"
    DISCOVERED = "discovered"
    LOCKED = "locked"
    ACQUIRED = "acquired"
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


@dataclass(frozen=True)
class RecoveryTarget:
    """The exact installation target Recovery must match."""

    product_id: str
    product_name: str
    version: str
    build: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]+", self.product_id):
            raise ValueError("Recovery product_id is invalid")
        if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", self.version):
            raise ValueError("Recovery version must be an exact dotted version")
        if not re.fullmatch(r"\d{2}[A-Z]\d{2,6}[a-z]?", self.build):
            raise ValueError("Recovery build is invalid")
        if not self.product_name.strip():
            raise ValueError("Recovery product_name is required")

    def to_dict(self) -> Dict[str, str]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "version": self.version,
            "build": self.build,
        }

    @property
    def digest(self) -> str:
        return canonical_json_digest(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryTarget":
        return cls(str(data["product_id"]), str(data["product_name"]), str(data["version"]), str(data["build"]))


@dataclass(frozen=True)
class RecoveryProduct:
    """An Apple server response after parsing and policy validation."""

    target: RecoveryTarget
    image_url: str
    image_sha256: str
    image_size_bytes: Optional[int]
    chunklist_url: str
    chunklist_sha256: str
    chunklist_size_bytes: Optional[int]
    image_session_ref: str
    chunklist_session_ref: str
    authentication: str = "apple-signed-chunklist"

    def __post_init__(self) -> None:
        for name, value in (("image_sha256", self.image_sha256), ("chunklist_sha256", self.chunklist_sha256)):
            if not re.fullmatch(r"[0-9a-f]{64}", value.lower()):
                raise ValueError(f"{name} must be a SHA-256 hex digest")
        for name, size_value in (("image_size_bytes", self.image_size_bytes), ("chunklist_size_bytes", self.chunklist_size_bytes)):
            if size_value is not None and (not isinstance(size_value, int) or isinstance(size_value, bool) or size_value <= 0):
                raise ValueError(f"{name} must be positive when present")
        if self.authentication != "apple-signed-chunklist":
            raise ValueError("Recovery authentication must use Apple's signed chunklist")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "image_url": self.image_url,
            "image_sha256": self.image_sha256,
            "image_size_bytes": self.image_size_bytes,
            "chunklist_url": self.chunklist_url,
            "chunklist_sha256": self.chunklist_sha256,
            "chunklist_size_bytes": self.chunklist_size_bytes,
            "image_session_ref": "<private>",
            "chunklist_session_ref": "<private>",
            "authentication": self.authentication,
        }

    @property
    def digest(self) -> str:
        return canonical_json_digest(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryProduct":
        target_data = data.get("target")
        if not isinstance(target_data, dict):
            raise ValueError("Recovery lock product target is missing")
        return cls(
            target=RecoveryTarget.from_dict(target_data),
            image_url=str(data["image_url"]),
            image_sha256=str(data["image_sha256"]),
            image_size_bytes=data.get("image_size_bytes"),
            chunklist_url=str(data["chunklist_url"]),
            chunklist_sha256=str(data["chunklist_sha256"]),
            chunklist_size_bytes=data.get("chunklist_size_bytes"),
            image_session_ref="<private>",
            chunklist_session_ref="<private>",
            authentication=str(data.get("authentication", "apple-signed-chunklist")),
        )


@dataclass(frozen=True)
class RecoveryBinding:
    """Transitive binding that prevents Recovery being reused for another build."""

    target_digest: str
    stable_model_id: str
    configuration_digest: str
    build_plan_digest: str
    policy_digest: str
    catalog_digest: str
    toolchain_digest: str
    efi_manifest_digest: str

    def __post_init__(self) -> None:
        values = (
            self.target_digest, self.configuration_digest, self.build_plan_digest,
            self.policy_digest, self.catalog_digest, self.toolchain_digest,
            self.efi_manifest_digest,
        )
        if any(not re.fullmatch(r"[0-9a-f]{64}", value.lower()) for value in values):
            raise ValueError("Recovery binding digests must be SHA-256 hex values")
        if not self.stable_model_id.strip():
            raise ValueError("Recovery binding requires a stable model ID")

    def to_dict(self) -> Dict[str, str]:
        return {
            "target_digest": self.target_digest,
            "stable_model_id": self.stable_model_id,
            "configuration_digest": self.configuration_digest,
            "build_plan_digest": self.build_plan_digest,
            "policy_digest": self.policy_digest,
            "catalog_digest": self.catalog_digest,
            "toolchain_digest": self.toolchain_digest,
            "efi_manifest_digest": self.efi_manifest_digest,
        }

    @property
    def digest(self) -> str:
        return canonical_json_digest(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryBinding":
        return cls(
            target_digest=str(data["target_digest"]),
            stable_model_id=str(data["stable_model_id"]),
            configuration_digest=str(data["configuration_digest"]),
            build_plan_digest=str(data["build_plan_digest"]),
            policy_digest=str(data["policy_digest"]),
            catalog_digest=str(data["catalog_digest"]),
            toolchain_digest=str(data["toolchain_digest"]),
            efi_manifest_digest=str(data["efi_manifest_digest"]),
        )


@dataclass(frozen=True)
class RecoveryLock:
    """Exact product and binding accepted for acquisition."""

    schema_version: str
    state: RecoveryState
    product: RecoveryProduct
    binding: RecoveryBinding
    source_policy_digest: str
    discovery_record_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise ValueError("unsupported Recovery lock schema")
        if self.state not in {RecoveryState.LOCKED, RecoveryState.ACQUIRED, RecoveryState.VERIFIED}:
            raise ValueError("Recovery lock must be locked, acquired or verified")
        for name, value in (("source_policy_digest", self.source_policy_digest), ("discovery_record_digest", self.discovery_record_digest)):
            if not re.fullmatch(r"[0-9a-f]{64}", value.lower()):
                raise ValueError(f"{name} must be a SHA-256 hex value")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "product": self.product.to_dict(),
            "binding": self.binding.to_dict(),
            "source_policy_digest": self.source_policy_digest,
            "discovery_record_digest": self.discovery_record_digest,
        }

    @property
    def digest(self) -> str:
        return canonical_json_digest(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryLock":
        product_data = data.get("product")
        binding_data = data.get("binding")
        if not isinstance(product_data, dict) or not isinstance(binding_data, dict):
            raise ValueError("Recovery lock product or binding is missing")
        return cls(
            schema_version=str(data["schema_version"]),
            state=RecoveryState(str(data["state"])),
            product=RecoveryProduct.from_dict(product_data),
            binding=RecoveryBinding.from_dict(binding_data),
            source_policy_digest=str(data["source_policy_digest"]),
            discovery_record_digest=str(data["discovery_record_digest"]),
        )

    def assert_current(self, binding: RecoveryBinding, target: RecoveryTarget) -> None:
        if self.product.target != target or self.binding != binding:
            raise ValueError("Recovery lock is stale for the requested target or build")


@dataclass(frozen=True)
class RecoveryEvidence:
    """Redacted proof of a verified bundle; session tokens never leave memory."""

    lock_digest: str
    image_digest: str
    chunklist_digest: str
    signed_chunklist: bool
    verified_chunks: int
    image_size_bytes: int
    verification_tool: str

    def __post_init__(self) -> None:
        for name, value in (("lock_digest", self.lock_digest), ("image_digest", self.image_digest), ("chunklist_digest", self.chunklist_digest)):
            if not re.fullmatch(r"[0-9a-f]{64}", value.lower()):
                raise ValueError(f"{name} must be a SHA-256 hex value")
        if not self.signed_chunklist or self.verified_chunks <= 0 or self.image_size_bytes <= 0:
            raise ValueError("Recovery evidence must prove a signed, non-empty chunklist readback")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lock_digest": self.lock_digest,
            "image_digest": self.image_digest,
            "chunklist_digest": self.chunklist_digest,
            "signed_chunklist": self.signed_chunklist,
            "verified_chunks": self.verified_chunks,
            "image_size_bytes": self.image_size_bytes,
            "verification_tool": self.verification_tool,
        }
