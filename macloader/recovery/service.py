"""Shared Recovery service used by CLI and Textual clients."""

from dataclasses import dataclass
import hashlib
import json
import os
import shutil
import re
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Optional

import yaml

from macloader.domain.recovery import RecoveryBinding, RecoveryEvidence, RecoveryLock, RecoveryProduct, RecoveryState, RecoveryTarget
from macloader.domain.build_plan import BuildPlan
from macloader.domain.configuration import UserConfiguration
from macloader.domain.contracts import BuildManifest, ToolchainSelection, canonical_json_digest
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError
from macloader.recovery.acquirer import RecoveryAcquirer, RecoveryAsset, RecoveryBundle, verify_apple_chunklist
from macloader.recovery.discovery import AppleRecoveryDiscovery, DiscoveryResponse, RecoveryDiscoveryResult


MAX_RECOVERY_STATE_BYTES = 4 * 1024 * 1024
MAX_RECOVERY_STATE_DEPTH = 32
MAX_RECOVERY_STATE_NODES = 10_000
MAX_RECOVERY_STATE_STRING = 65_536


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

    def __init__(self, policy: Optional[RecoveryPolicy] = None, *, workflow_capability: Optional[object] = None):
        self.policy = policy or load_recovery_policy()
        self._workflow_capability = workflow_capability or object()
        # A binding is only eligible for the strict acquisition path when it
        # was issued by this live service instance after the shared workflow
        # checked the current plan, catalog, toolchain and EFI manifest.
        self._verification_token = object()
        self._verified_binding_digests: set[str] = set()

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

    def derive_binding(
        self,
        configuration: UserConfiguration,
        plan: BuildPlan,
        toolchain: ToolchainSelection,
        manifest: BuildManifest,
        catalog_digest: str,
        *,
        expected_artifact_lock_digest: str,
        expected_build_digest: str,
        expected_profile_digest: str,
        expected_evidence_digests: tuple[str, ...],
        expected_identity_reference: Optional[str] = None,
        workflow_capability: Optional[object] = None,
    ) -> RecoveryBinding:
        """Derive a Recovery binding from the current verified artifacts.

        Callers never need to author binding JSON.  The manifest and plan are
        checked for the exact policy target before their canonical identities
        are published into the lock.
        """
        if (
            manifest.target_macos.lower() != self.policy.target.product_id
            or manifest.target_model != plan.target_model
            or plan.target_version != self.policy.target.version
            or plan.target_build != self.policy.target.build
        ):
            raise ValueError("EFI manifest and BuildPlan do not match the exact Recovery policy target")
        if manifest.validation_report != "VALID":
            raise ValueError("Recovery binding requires a qualified validated EFI manifest")
        if not catalog_digest or len(catalog_digest) != 64:
            raise ValueError("Recovery binding requires the current dependency catalog digest")
        if manifest.toolchain_digest.lower() != toolchain.digest.lower():
            raise ValueError("EFI manifest toolchain identity does not match the trusted selection")
        if manifest.build_digest.lower() != expected_build_digest.lower():
            raise ValueError("EFI manifest build identity does not match the current configuration and dependency lock")
        if manifest.artifact_lock_digest.lower() != expected_artifact_lock_digest.lower():
            raise ValueError("EFI manifest artifact lock does not match the current dependency resolution")
        if manifest.profile_digest.lower() != expected_profile_digest.lower():
            raise ValueError("EFI manifest profile does not match the current reviewed profile")
        if tuple(item.lower() for item in manifest.evidence_digests) != tuple(item.lower() for item in expected_evidence_digests):
            raise ValueError("EFI manifest evidence does not match the current verified machine evidence")
        if expected_identity_reference is not None and manifest.identity_reference != expected_identity_reference:
            raise ValueError("EFI manifest identity reference does not match the selected private identity")
        binding = RecoveryBinding(
            target_digest=self.policy.target.digest,
            stable_model_id=plan.stable_model_id or manifest.target_model,
            configuration_digest=configuration.semantic_digest,
            build_plan_digest=plan.canonical_digest(),
            policy_digest=self.policy.digest,
            catalog_digest=catalog_digest.lower(),
            toolchain_digest=toolchain.digest,
            efi_manifest_digest=canonical_json_digest(manifest.to_dict()),
            _verification_token=(
                self._verification_token
                if workflow_capability is self._workflow_capability
                else None
            ),
        )
        if binding._verification_token is self._verification_token:
            self._verified_binding_digests.add(binding.digest)
        return binding

    def lock(
        self,
        result: RecoveryDiscoveryResult,
        binding: RecoveryBinding,
        *,
        verified_artifacts: Optional[Mapping[str, str]] = None,
        require_verified: bool = False,
    ) -> RecoveryLock:
        if result.state != RecoveryState.DISCOVERED or result.product is None:
            raise ValueError("Only an exact discovered Recovery product can be locked")
        if result.product.target != self.policy.target:
            raise ValueError("Recovery product does not match the policy target")
        if binding.target_digest.lower() != self.policy.target.digest.lower():
            raise ValueError("Recovery binding target digest does not match the policy target")
        if binding.policy_digest.lower() != self.policy.digest.lower():
            raise ValueError("Recovery binding policy digest is stale")
        if require_verified and verified_artifacts is None:
            raise ValueError("Recovery binding must be derived from current verified artifacts")
        if require_verified and (
            binding._verification_token is not self._verification_token
            or binding.digest not in self._verified_binding_digests
        ):
            raise ValueError("Recovery binding was not issued by the current verified workflow")
        if verified_artifacts is not None:
            expected = {
                "configuration_digest": binding.configuration_digest,
                "build_plan_digest": binding.build_plan_digest,
                "catalog_digest": binding.catalog_digest,
                "toolchain_digest": binding.toolchain_digest,
                "efi_manifest_digest": binding.efi_manifest_digest,
            }
            if dict(verified_artifacts) != expected:
                raise ValueError("Recovery binding does not match the current verified artifacts")
        return RecoveryLock("1", RecoveryState.LOCKED, result.product, binding, self.policy.digest, result.record_digest)

    @staticmethod
    def load_lock(path: Path) -> RecoveryLock:
        try:
            payload = RecoveryService._read_bounded_json(Path(path), "Recovery lock")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError("Recovery lock file is unreadable or malformed") from exc
        if not isinstance(payload, dict):
            raise ValueError("Recovery lock file must contain an object")
        lock = RecoveryLock.from_dict(payload)
        state_path = Path(path).parent / "recovery.state.json"
        if state_path.is_symlink() or not state_path.is_file():
            raise ValueError("Recovery lock has no authoritative published state")
        state_lock, _ = RecoveryService._load_published_state(Path(path).parent)
        if lock.to_dict() != state_lock.to_dict():
            raise ValueError("Recovery lock does not match the authoritative published state")
        return lock

    @staticmethod
    def save_lock(lock: RecoveryLock, path: Path) -> None:
        RecoveryService._atomic_json_write(Path(path), lock.to_dict())

    @staticmethod
    def save_evidence(evidence: RecoveryEvidence, path: Path) -> None:
        RecoveryService._atomic_json_write(Path(path), evidence.to_dict())

    @staticmethod
    def save_verified_bundle(lock: RecoveryLock, evidence: RecoveryEvidence, destination: Path) -> tuple[Path, Path, Path]:
        """Publish one authoritative lock/evidence record before compatibility files.

        The combined state file is the transaction marker.  Readers can reject
        a directory whose compatibility files do not agree with it, instead of
        treating a partially published pair as a current result.
        """
        destination = Path(destination)
        if evidence.lock_digest.lower() != lock.digest.lower():
            raise ValueError("Recovery evidence is bound to a different lock identity")
        state_path = destination / "recovery.state.json"
        RecoveryService._atomic_json_write(
            state_path,
            {"schema_version": "1", "lock": lock.to_dict(), "evidence": evidence.to_dict()},
        )
        lock_path = destination / "recovery.lock.json"
        evidence_path = destination / "recovery.evidence.json"
        RecoveryService.save_lock(lock, lock_path)
        RecoveryService.save_evidence(evidence, evidence_path)
        RecoveryService._load_published_state(destination)
        return lock_path, evidence_path, state_path

    @staticmethod
    def _load_published_state(destination: Path) -> tuple[RecoveryLock, RecoveryEvidence]:
        """Load the state marker and compatibility files as one generation."""
        destination = Path(destination)
        state_path = destination / "recovery.state.json"
        lock_path = destination / "recovery.lock.json"
        evidence_path = destination / "recovery.evidence.json"
        paths = (state_path, lock_path, evidence_path)
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise ValueError("Published Recovery state is incomplete or unsafe")
        try:
            state = RecoveryService._read_bounded_json(state_path, "Recovery state")
            lock_data = RecoveryService._read_bounded_json(lock_path, "Recovery lock")
            evidence_data = RecoveryService._read_bounded_json(evidence_path, "Recovery evidence")
        except (OSError, ValueError) as exc:
            raise ValueError("Published Recovery state is unreadable") from exc
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != "1"
            or not isinstance(state.get("lock"), dict)
            or not isinstance(state.get("evidence"), dict)
            or not isinstance(lock_data, dict)
            or not isinstance(evidence_data, dict)
            or state["lock"] != lock_data
            or state["evidence"] != evidence_data
        ):
            raise ValueError("Published Recovery compatibility files do not match the authoritative state")
        lock = RecoveryLock.from_dict(lock_data)
        try:
            evidence = RecoveryEvidence(**evidence_data)
        except (TypeError, ValueError) as exc:
            raise ValueError("Published Recovery evidence is malformed") from exc
        if evidence.lock_digest.lower() != lock.digest.lower():
            raise ValueError("Published Recovery evidence is bound to a different lock")
        return lock, evidence

    @staticmethod
    def _read_bounded_json(path: Path, label: str) -> object:
        try:
            if path.stat().st_size > MAX_RECOVERY_STATE_BYTES:
                raise ValueError(f"{label} exceeds the bounded state size")
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"{label} is unreadable") from exc
        if len(raw) > MAX_RECOVERY_STATE_BYTES:
            raise ValueError(f"{label} exceeds the bounded state size")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} is malformed JSON") from exc
        RecoveryService._check_json_limits(value, label)
        return value

    @staticmethod
    def _check_json_limits(value: object, label: str) -> None:
        """Reject pathological nested Recovery metadata after bounded parsing."""
        stack: list[tuple[object, int]] = [(value, 0)]
        nodes = 0
        while stack:
            current, depth = stack.pop()
            nodes += 1
            if nodes > MAX_RECOVERY_STATE_NODES or depth > MAX_RECOVERY_STATE_DEPTH:
                raise ValueError(f"{label} exceeds bounded structural complexity")
            if isinstance(current, str):
                if len(current) > MAX_RECOVERY_STATE_STRING:
                    raise ValueError(f"{label} contains an oversized string")
            elif isinstance(current, dict):
                for key, item in current.items():
                    stack.append((key, depth + 1))
                    stack.append((item, depth + 1))
            elif isinstance(current, list):
                for item in current:
                    stack.append((item, depth + 1))

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
        verified_artifacts: Optional[Mapping[str, str]] = None,
        require_verified: bool = False,
    ) -> tuple[RecoveryLock, RecoveryBundle]:
        lock = self.lock(
            result,
            binding,
            verified_artifacts=verified_artifacts,
            require_verified=require_verified,
        )
        image, chunklist = self._assets_for(lock.product)
        destination = Path(destination)
        self._assert_safe_directory(destination)
        destination.mkdir(parents=True, exist_ok=True)
        self._assert_safe_directory(destination)
        image_path = destination / f"Recovery-{lock.product.target.build}.dmg"
        chunklist_path = destination / f"Recovery-{lock.product.target.build}.chunklist"
        free_bytes = shutil.disk_usage(destination).free
        retained = 0
        if resume:
            staging = destination / f".{image_path.stem}.recovery-resume"
            for asset, staged_name in ((image, "image.part"), (chunklist, "chunklist.part")):
                partial, metadata = RecoveryAcquirer._resume_paths(asset, staging / staged_name)
                if (
                    partial.is_file()
                    and not partial.is_symlink()
                    and RecoveryAcquirer._resume_metadata_matches(metadata, asset)
                ):
                    retained += min(partial.stat().st_size, asset.size_bytes)
        required_new_bytes = max(0, image.size_bytes + chunklist.size_bytes - retained)
        if free_bytes < self.policy.minimum_free_bytes + required_new_bytes:
            raise ArtifactDownloadError(
                "Recovery acquisition blocked by the free-space reserve: "
                f"required={self.policy.minimum_free_bytes + required_new_bytes} available={free_bytes}"
            )
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

    def verify(
        self,
        lock: RecoveryLock,
        image_path: Path,
        chunklist_path: Path,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> RecoveryEvidence:
        if (
            lock.source_policy_digest.lower() != self.policy.digest.lower()
            or lock.product.target != self.policy.target
            or lock.binding.target_digest.lower() != self.policy.target.digest.lower()
            or lock.binding.policy_digest.lower() != self.policy.digest.lower()
        ):
            raise ValueError("Recovery lock was created from a stale policy")
        state_path = Path(image_path).parent / "recovery.state.json"
        if state_path.is_symlink() or not state_path.is_file():
            raise ValueError("Recovery verification requires an authoritative published state")
        published_lock, _ = self._load_published_state(Path(image_path).parent)
        if published_lock.to_dict() != lock.to_dict():
            raise ValueError("Recovery lock does not match the authoritative published state")
        if cancel is None:
            verified_chunks, image_size = verify_apple_chunklist(
                Path(image_path), Path(chunklist_path), lock.product.image_sha256, lock.product.chunklist_sha256,
            )
        else:
            verified_chunks, image_size = verify_apple_chunklist(
                Path(image_path), Path(chunklist_path), lock.product.image_sha256, lock.product.chunklist_sha256,
                cancel=cancel,
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
