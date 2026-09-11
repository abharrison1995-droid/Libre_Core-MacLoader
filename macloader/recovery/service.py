"""Shared Recovery service used by CLI and Textual clients."""

from dataclasses import dataclass
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Optional

import yaml

from macloader.domain.recovery import RecoveryBinding, RecoveryEvidence, RecoveryLock, RecoveryProduct, RecoveryState, RecoveryTarget
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError
from macloader.recovery.acquirer import RecoveryAcquirer, RecoveryAsset, RecoveryBundle, verify_apple_chunklist
from macloader.recovery.discovery import AppleRecoveryDiscovery, DiscoveryResponse, RecoveryDiscoveryResult


@dataclass(frozen=True)
class RecoveryPolicy:
    policy_id: str
    schema_version: str
    tool_version: str
    tool_digest: str
    target: RecoveryTarget
    board_id: str
    discovery_host: str
    query_scheme: str
    asset_hosts: tuple[str, ...]
    authentication: str
    minimum_free_bytes: int
    digest: str


def load_recovery_policy(path: Optional[Path] = None) -> RecoveryPolicy:
    policy_path = path or Path(__file__).resolve().parent.parent / "database" / "data" / "recovery" / "catalog.yaml"
    raw_bytes = policy_path.read_bytes()
    raw = yaml.safe_load(raw_bytes)
    if not isinstance(raw, dict) or raw.get("schema_version") != "1" or not isinstance(raw.get("targets"), list) or len(raw["targets"]) != 1:
        raise ValueError("Recovery catalog must contain exactly one schema-1 target")
    tool = raw.get("tool")
    target_data = raw["targets"][0]
    if not isinstance(tool, dict) or not isinstance(target_data, dict):
        raise ValueError("Recovery catalog tool and target must be mappings")
    if str(raw.get("policy_id")) != "recovery-apple-2026-09":
        raise ValueError("Recovery catalog policy ID is not trusted")
    if str(tool.get("version")) != "1.0.7" or not re.fullmatch(r"[0-9a-f]{64}", str(tool.get("digest", "")).lower()):
        raise ValueError("Recovery catalog tool record is not trusted")
    target = RecoveryTarget(
        str(target_data["product_id"]), str(target_data["product_name"]),
        str(target_data["version"]), str(target_data["build"]),
    )
    if target.build != "24A335" or target.version != "15.0":
        raise ValueError("Recovery policy target drifted from Sequoia 15.0/24A335")
    allowed_hosts = tuple(str(item) for item in target_data.get("asset_hosts", []))
    if set(allowed_hosts) != {"osrecovery.apple.com", "updates.cdn-apple.com", "cdn-apple.com"}:
        raise ValueError("Recovery policy must declare asset hosts")
    if str(target_data.get("discovery_host")) != "osrecovery.apple.com" or str(target_data.get("query_scheme")) != "https":
        raise ValueError("Recovery policy must use the pinned HTTPS Apple discovery endpoint")
    if str(target_data.get("authentication")) != "apple-signed-chunklist":
        raise ValueError("Recovery policy must require signed chunklists")
    return RecoveryPolicy(
        policy_id=str(raw.get("policy_id", "")), schema_version="1",
        tool_version=str(tool["version"]), tool_digest=str(tool["digest"]).lower(),
        target=target, board_id=str(target_data["board_id"]),
        discovery_host=str(target_data["discovery_host"]), query_scheme=str(target_data["query_scheme"]),
        asset_hosts=allowed_hosts, authentication=str(target_data["authentication"]),
        minimum_free_bytes=int(target_data["minimum_free_bytes"]),
        digest=hashlib.sha256(raw_bytes).hexdigest(),
    )


class RecoveryService:
    """One Recovery policy boundary for CLI, TUI and future media planning."""

    def __init__(self, policy: Optional[RecoveryPolicy] = None):
        self.policy = policy or load_recovery_policy()

    def target(self) -> RecoveryTarget:
        return self.policy.target

    def discover(
        self,
        transport: Optional[Callable[[str, Mapping[str, str], bytes], DiscoveryResponse]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> RecoveryDiscoveryResult:
        client = AppleRecoveryDiscovery(
            transport=transport,
            board_id=self.policy.board_id,
            tool_version=self.policy.tool_version,
            discovery_host=self.policy.discovery_host,
            query_scheme=self.policy.query_scheme,
            asset_hosts=self.policy.asset_hosts,
            cancel=cancel,
        )
        return client.discover(self.policy.target)

    def lock(self, result: RecoveryDiscoveryResult, binding: RecoveryBinding) -> RecoveryLock:
        if result.state != RecoveryState.DISCOVERED or result.product is None:
            raise ValueError("Only an exact discovered Recovery product can be locked")
        if result.product.target != self.policy.target:
            raise ValueError("Recovery product does not match the policy target")
        if binding.target_digest.lower() != self.policy.target.digest.lower():
            raise ValueError("Recovery binding target digest does not match the policy target")
        if binding.policy_digest.lower() != self.policy.digest.lower():
            raise ValueError("Recovery binding policy digest is stale")
        return RecoveryLock("1", RecoveryState.LOCKED, result.product, binding, self.policy.digest, result.record_digest)

    @staticmethod
    def load_lock(path: Path) -> RecoveryLock:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError("Recovery lock file is unreadable or malformed") from exc
        if not isinstance(payload, dict):
            raise ValueError("Recovery lock file must contain an object")
        return RecoveryLock.from_dict(payload)

    @staticmethod
    def save_lock(lock: RecoveryLock, path: Path) -> None:
        RecoveryService._atomic_json_write(Path(path), lock.to_dict())

    @staticmethod
    def save_evidence(evidence: RecoveryEvidence, path: Path) -> None:
        RecoveryService._atomic_json_write(Path(path), evidence.to_dict())

    def _assets_for(self, product: RecoveryProduct) -> tuple[RecoveryAsset, RecoveryAsset]:
        if product.image_size_bytes is None or product.chunklist_size_bytes is None:
            raise ArtifactDownloadError(
                "Apple discovery did not declare bounded Recovery asset sizes; acquisition is blocked"
            )
        return (
            RecoveryAsset(
                product="InstallAssistant",
                build=product.target.build,
                source_url=product.image_url,
                sha256=product.image_sha256,
                size_bytes=product.image_size_bytes,
                session_token=product.image_session_ref,
            ),
            RecoveryAsset(
                product="InstallAssistant",
                build=product.target.build,
                source_url=product.chunklist_url,
                sha256=product.chunklist_sha256,
                size_bytes=product.chunklist_size_bytes,
                session_token=product.chunklist_session_ref,
            ),
        )

    def acquire(
        self,
        result: RecoveryDiscoveryResult,
        binding: RecoveryBinding,
        destination: Path,
        transport: Optional[Callable[[str, Path], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
        resume: bool = False,
    ) -> tuple[RecoveryLock, RecoveryBundle]:
        lock = self.lock(result, binding)
        image, chunklist = self._assets_for(lock.product)
        destination = Path(destination)
        self._assert_safe_directory(destination)
        destination.mkdir(parents=True, exist_ok=True)
        self._assert_safe_directory(destination)
        image_path = destination / f"Recovery-{lock.product.target.build}.dmg"
        chunklist_path = destination / f"Recovery-{lock.product.target.build}.chunklist"
        bundle = RecoveryAcquirer(transport=transport, cancel=cancel, resume=resume).download_bundle(
            image,
            chunklist,
            image_path,
            chunklist_path,
            lock.digest,
            resume=resume,
        )
        verified_lock = RecoveryLock(
            lock.schema_version,
            RecoveryState.VERIFIED,
            lock.product,
            lock.binding,
            lock.source_policy_digest,
            lock.discovery_record_digest,
        )
        return verified_lock, bundle

    def verify(self, lock: RecoveryLock, image_path: Path, chunklist_path: Path) -> RecoveryEvidence:
        if (
            lock.source_policy_digest.lower() != self.policy.digest.lower()
            or lock.product.target != self.policy.target
            or lock.binding.target_digest.lower() != self.policy.target.digest.lower()
            or lock.binding.policy_digest.lower() != self.policy.digest.lower()
        ):
            raise ValueError("Recovery lock was created from a stale policy")
        verified_chunks, image_size = verify_apple_chunklist(
            Path(image_path), Path(chunklist_path), lock.product.image_sha256, lock.product.chunklist_sha256
        )
        if lock.product.image_size_bytes is not None and image_size != lock.product.image_size_bytes:
            raise ChecksumMismatchError("Recovery image size does not match the locked product")
        return RecoveryEvidence(
            lock_digest=lock.digest,
            image_digest=lock.product.image_sha256,
            chunklist_digest=lock.product.chunklist_sha256,
            signed_chunklist=True,
            verified_chunks=verified_chunks,
            image_size_bytes=image_size,
            verification_tool="OpenCore-macrecovery-1.0.7-compatible",
        )

    @staticmethod
    def _assert_safe_directory(path: Path) -> None:
        absolute = path.absolute()
        for ancestor in (absolute, *absolute.parents):
            if ancestor.is_symlink():
                raise ArtifactDownloadError("Recovery destination contains a symlinked directory")
        if path.exists() and not path.is_dir():
            raise ArtifactDownloadError("Recovery destination is not a directory")

    @staticmethod
    def _atomic_json_write(path: Path, payload: object) -> None:
        path = Path(path)
        RecoveryService._assert_safe_directory(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        RecoveryService._assert_safe_directory(path.parent)
        if path.is_symlink():
            raise ArtifactDownloadError("Recovery metadata destination must not be a symlink")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            RecoveryService._assert_safe_directory(path.parent)
            if path.is_symlink():
                raise ArtifactDownloadError("Recovery metadata destination became a symlink")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
