"""Regression tests for the consolidated readiness repair boundaries."""

import asyncio
from dataclasses import replace
import hashlib
import json
import plistlib
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from macloader.build.config import SchemaDrivenConfigGenerator, effective_profile_digest, load_reviewed_profile
from macloader.build.acpi import AcpiProcessor
from macloader.build.efi import EfiBuilder
from macloader.configuration.store import ConfigurationStore, ConfigurationStoreError
from macloader.database.loader import Database
from macloader.dependencies.archive import safe_extract_zip
from macloader.dependencies.cache import CacheManager
from macloader.dependencies.downloader import Downloader
from macloader.dependencies.graph import DependencyGraph
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.configuration import UserConfiguration
from macloader.domain.contracts import BuildManifest, IdentityReference, ToolchainSelection, canonical_json_digest
from macloader.domain.dependencies import ArtifactVariant, DependencyArtifact
from macloader.domain.evidence import EvidenceCompleteness, EvidenceConfidence, EvidenceRecord
from macloader.domain.recovery import RecoveryBinding, RecoveryEvidence, RecoveryLock, RecoveryProduct, RecoveryState
from macloader.exceptions import ArchiveSecurityError, ArtifactDownloadError, BuildPlanError
from macloader.identity.service import IdentityService, IdentityServiceError
from macloader.removable.writer import (
    DestructiveConfirmation, MediaBindings, RemovableDevice, RemovableMediaWriter,
    UnsafeRemovableTarget, WritePlan,
)
from macloader.ui.tui import WorkflowApp
from macloader.workflow.service import WorkflowService
from macloader.recovery.service import MAX_RECOVERY_STATE_BYTES, RecoveryService, load_recovery_policy
from macloader.recovery.acquirer import verify_apple_chunklist
from macloader.recovery.discovery import RecoveryDiscoveryResult


def test_configuration_compare_and_swap_rejects_higher_numbered_stale_draft(tmp_path: Path) -> None:
    store_a = ConfigurationStore(tmp_path / "configs")
    store_b = ConfigurationStore(tmp_path / "configs")
    base = UserConfiguration(configuration_id="draft", revision=0)
    store_a.save(base, expected_revision=0)
    loaded_a = store_a.load("draft")
    loaded_b = store_b.load("draft")
    store_a.save(replace(loaded_a, revision=1, hardware_snapshot_id="newer"), expected_revision=0)
    with pytest.raises(ConfigurationStoreError, match="revision conflict"):
        store_b.save(replace(loaded_b, revision=9, target=loaded_b.target), expected_revision=0)
    assert store_a.load("draft").hardware_snapshot_id == "newer"


def test_workflow_save_rejects_untracked_nonzero_revision(tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    with pytest.raises(ValueError, match="loaded before saving"):
        service.save(UserConfiguration(configuration_id="untracked", revision=99))


def test_workflow_save_revision_uses_each_loaded_drafts_base_revision(tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    service.save(UserConfiguration(configuration_id="shared", revision=0))
    first = service.load("shared")
    second = service.load("shared")

    service.save_revision(replace(first, hardware_snapshot_id="first"))
    with pytest.raises(ConfigurationStoreError, match="revision conflict"):
        service.save_revision(replace(second, hardware_snapshot_id="stale"))
    assert service.load("shared").hardware_snapshot_id == "first"


def test_workflow_save_revision_rejects_untracked_revision_base(tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    with pytest.raises(ValueError, match="loaded before saving"):
        service.save_revision(UserConfiguration(configuration_id="untracked", revision=0))


def test_configuration_store_requires_loaded_base_for_replacement(tmp_path: Path) -> None:
    store = ConfigurationStore(tmp_path / "configs")
    store.save(UserConfiguration(configuration_id="draft", revision=0), expected_revision=0)
    loaded = store.load("draft")
    with pytest.raises(ConfigurationStoreError, match="loaded base revision"):
        store.save(replace(loaded, revision=9))


def test_effective_profile_digest_includes_material_options() -> None:
    profile = load_reviewed_profile()
    assert effective_profile_digest(profile, {"profile.audio": "layout-11"}) != effective_profile_digest(
        profile, {"profile.audio": "layout-86"}
    )


def test_dependency_graph_checks_cancellation_during_traversal() -> None:
    graph = DependencyGraph()
    graph.add_node("opencore", [])
    with pytest.raises(ValueError, match="cancelled"):
        graph.resolve_ordered_set(["opencore"], cancel=lambda: True)


def test_recovery_state_reader_rejects_oversized_json(tmp_path: Path) -> None:
    path = tmp_path / "recovery.json"
    path.write_bytes(b"{" + b"x" * MAX_RECOVERY_STATE_BYTES + b"}")
    with pytest.raises(ValueError, match="bounded state size"):
        RecoveryService._read_bounded_json(path, "Recovery state")


def test_recovery_state_reader_rejects_pathological_nesting(tmp_path: Path) -> None:
    path = tmp_path / "recovery-nested.json"
    value: object = "ok"
    for _ in range(40):
        value = [value]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="bounded structural complexity"):
        RecoveryService._read_bounded_json(path, "Recovery state")


def test_acpi_interrupt_terminates_external_process(tmp_path: Path) -> None:
    class InterruptingProcess:
        returncode = None

        def communicate(self, timeout: float = 0.0) -> tuple[str, str]:
            raise KeyboardInterrupt

        def poll(self) -> None:
            return None if self.returncode is None else self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout: float = 0.0) -> int:
            self.returncode = -15
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    process = InterruptingProcess()
    with mock.patch("macloader.build.acpi.subprocess.Popen", return_value=process):
        with pytest.raises(KeyboardInterrupt):
            AcpiProcessor(Path("/bin/iasl"), "0" * 64, tmp_path / "work")._run(
                [], tmp_path / "diagnostic.txt"
            )
    assert process.returncode == -15


def test_efi_process_cleanup_kills_when_termination_fails() -> None:
    class StubbornProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            raise OSError("already gone")

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float = 0.0) -> int:
            return self.returncode or -9

    process = StubbornProcess()
    EfiBuilder._terminate_process(process)  # type: ignore[arg-type]
    assert process.killed is True


def test_external_cleanup_terminates_and_kills_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GroupProcess:
        pid = 1234

        def __init__(self) -> None:
            self.waits = 0

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise AssertionError("group cleanup should be used")

        def kill(self) -> None:
            raise AssertionError("group cleanup should be used")

        def wait(self, timeout: float = 0.0) -> int:
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("tool", timeout)
            return -9

    groups: list[tuple[int, int]] = []
    monkeypatch.setattr("macloader.build.acpi.os.getpgid", lambda pid: pid + 1)
    monkeypatch.setattr("macloader.build.acpi.os.killpg", lambda pgid, sig: groups.append((pgid, sig)))
    for cleanup in (AcpiProcessor._terminate_process, EfiBuilder._terminate_process):
        process = GroupProcess()
        cleanup(process)  # type: ignore[arg-type]
    assert len(groups) == 4


def test_tui_worker_publication_rechecks_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    app = WorkflowApp()
    app._operation_generation = 2
    published: list[str] = []
    monkeypatch.setattr(app, "_message", published.append)
    app._publish_message_if_current(1, "stale")
    app._publish_message_if_current(2, "current")
    assert published == ["current"]


def test_build_manifest_round_trips_through_strict_loader() -> None:
    manifest = BuildManifest(
        "0.1", "a" * 64, "Lenovo ThinkPad T480s", "sequoia", "b" * 64, "VALID",
        output_paths={"efi": "EFI"}, toolchain_digest="c" * 64, identity_digest="d" * 64,
        output_digest="e" * 64, license_digests={"opencore": "f" * 64},
        schema_digest="1" * 64, profile_digest="2" * 64, acpi_digest="3" * 64,
        evidence_digests=("4" * 64,), usb_policy_state="unresolved", usb_first_install_route="usb-a",
        identity_reference="private.json",
    )
    assert BuildManifest.from_dict(manifest.to_dict()) == manifest
    malformed = manifest.to_dict()
    malformed["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        BuildManifest.from_dict(malformed)


def test_pre_cancelled_and_interrupted_downloads_remove_owned_part_file(tmp_path: Path) -> None:
    content = b"payload"
    artifact = DependencyArtifact(
        "payload.zip", "https://example.invalid/payload.zip", hashlib.sha256(content).hexdigest(), len(content),
        variant=ArtifactVariant.RELEASE,
    )
    destination = tmp_path / "payload.zip"
    def write_payload(_url: str, path: Path) -> None:
        path.write_bytes(content)

    with pytest.raises(ArtifactDownloadError):
        Downloader(transport=write_payload).download_artifact(
            artifact, destination, cancel=lambda: True
        )
    assert list(tmp_path.glob("*.part")) == []

    def interrupt(_url: str, _path: Path) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        Downloader(transport=interrupt).download_artifact(artifact, destination)
    assert list(tmp_path.glob("*.part")) == []


def test_archive_extraction_honors_operation_cancellation(tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("EFI/OC/config.plist", b"config")
    with pytest.raises(ArchiveSecurityError, match="cancelled"):
        safe_extract_zip(archive, tmp_path / "out", cancel=lambda: True)


def test_long_running_validation_boundaries_honor_cancellation(tmp_path: Path) -> None:
    with pytest.raises(BuildPlanError, match="cancelled"):
        AcpiProcessor(Path("/bin/sh"), "0" * 64, tmp_path / "work")._run(
            ["-c", "sleep 10"], tmp_path / "diagnostic.txt", cancel=lambda: True
        )
    (tmp_path / "payload").write_bytes(b"payload")
    with pytest.raises(BuildPlanError, match="cancelled"):
        EfiBuilder._tree_digest(tmp_path, cancel=lambda: True)


def test_recovery_verification_honors_operation_cancellation(tmp_path: Path) -> None:
    image = tmp_path / "image"
    chunklist = tmp_path / "chunklist"
    image.write_bytes(b"image")
    chunklist.write_bytes(b"chunklist")
    with pytest.raises(ArtifactDownloadError, match="verification cancelled"):
        verify_apple_chunklist(
            image,
            chunklist,
            hashlib.sha256(image.read_bytes()).hexdigest(),
            hashlib.sha256(chunklist.read_bytes()).hexdigest(),
            cancel=lambda: True,
        )


def test_cache_artifact_lease_blocks_clear_until_build_finishes(tmp_path: Path) -> None:
    db = Database()
    spec = db.get_dependency_spec("lilu")
    assert spec is not None
    artifact = spec.get_artifact(ArtifactVariant.RELEASE)
    assert artifact is not None
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"leased artifact")
    artifact.sha256 = hashlib.sha256(payload.read_bytes()).hexdigest()
    cache = CacheManager(tmp_path / "cache")
    cached = cache.put_artifact(spec, ArtifactVariant.RELEASE, payload)
    with cache.artifact_lease(spec, ArtifactVariant.RELEASE):
        cache.clear_cache()
        assert cached.is_file()
    cache.clear_cache()
    assert not cached.exists()


def test_cache_verification_leaves_a_corrupt_entry_for_the_publisher_to_replace(tmp_path: Path) -> None:
    db = Database()
    spec = db.get_dependency_spec("lilu")
    assert spec is not None
    artifact = spec.get_artifact(ArtifactVariant.RELEASE)
    assert artifact is not None
    cache = CacheManager(tmp_path / "cache")
    cached = cache.get_artifact_cache_path(spec, ArtifactVariant.RELEASE)
    cached.write_bytes(b"stale verifier bytes")
    assert cache.has_valid_artifact(spec, ArtifactVariant.RELEASE) is False
    assert cached.read_bytes() == b"stale verifier bytes"


def test_identity_reference_rejects_paths_and_non_json_names(tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    with pytest.raises(ValueError, match="local redacted JSON filename"):
        service.set_identity_reference(UserConfiguration(), "../private.json")
    with pytest.raises(ValueError, match="local redacted JSON filename"):
        service.set_identity_reference(UserConfiguration(), "identity.txt")


def test_public_configuration_export_redacts_private_evidence_and_identity(tmp_path: Path) -> None:
    record = EvidenceRecord(
        "acpi-1", "acpi", "1", "a" * 64, "private/acpi.json", "snapshot", "bios",
        "fixture", "1", EvidenceCompleteness.COMPLETE, EvidenceConfidence.HIGH,
        physical_port_evidence=True,
    )
    configuration = UserConfiguration(evidence=(record,), identity_ref=IdentityReference("0.1", "private.json"))
    exported = ConfigurationStore(tmp_path / "configs").export_public(configuration)
    assert exported["identity_ref"] is None
    assert exported["evidence"][0]["private_ref"] == "<private>"
    assert exported["evidence"][0]["completeness"] == "missing"
    assert exported["evidence"][0]["physical_port_evidence"] is False

def test_effective_audio_selection_changes_generated_plist(tmp_path: Path) -> None:
    sample = tmp_path / "Sample.plist"
    sample.write_bytes(plistlib.dumps({
        "ACPI": {}, "Booter": {}, "DeviceProperties": {}, "Kernel": {}, "Misc": {},
        "NVRAM": {}, "PlatformInfo": {"Generic": {}}, "UEFI": {},
    }, sort_keys=False))
    generator = SchemaDrivenConfigGenerator(sample, hashlib.sha256(sample.read_bytes()).hexdigest())
    profile = load_reviewed_profile()
    identity = IdentityService.fake_identity()

    def generate(layout: str) -> dict[str, object]:
        return generator.generate(
            profile, identity=identity, kexts=(), drivers=(), acpi_files=(),
            opencore_version="1.0.7", acpi_digest="", evidence_digests=(),
            effective_options={"profile.audio": layout},
        )

    eleven = generate("layout-11")
    eighty_six = generate("layout-86")
    audio_path = profile.audio.device_path
    assert int.from_bytes(eleven["DeviceProperties"]["Add"][audio_path]["layout-id"], "little") == 11  # type: ignore[index]
    assert int.from_bytes(eighty_six["DeviceProperties"]["Add"][audio_path]["layout-id"], "little") == 86  # type: ignore[index]
    assert eleven["NVRAM"] != eighty_six["NVRAM"]


def test_recovery_lock_digest_is_stable_across_lifecycle_state() -> None:
    digest = "a" * 64
    target = __import__("macloader.recovery.service", fromlist=["load_recovery_policy"]).load_recovery_policy().target
    product = RecoveryProduct(target, "https://updates.cdn-apple.com/image", digest, 1, "https://updates.cdn-apple.com/chunk", digest, 1, "<private>", "<private>")
    binding = RecoveryBinding(target.digest, "thinkpad-t480s", *([digest] * 6))
    locked = RecoveryLock("1", RecoveryState.LOCKED, product, binding, digest, digest)
    verified = replace(locked, state=RecoveryState.VERIFIED)
    assert locked.digest == verified.digest


def test_recovery_verified_state_publication_keeps_lock_and_evidence_together(tmp_path: Path) -> None:
    digest = "a" * 64
    target = __import__("macloader.recovery.service", fromlist=["load_recovery_policy"]).load_recovery_policy().target
    product = RecoveryProduct(target, "https://updates.cdn-apple.com/image", digest, 1, "https://updates.cdn-apple.com/chunk", digest, 1, "<private>", "<private>")
    binding = RecoveryBinding(target.digest, "thinkpad-t480s", *([digest] * 6))
    lock = RecoveryLock("1", RecoveryState.VERIFIED, product, binding, digest, digest)
    evidence = RecoveryEvidence(lock.digest, digest, digest, True, 1, 1, "test")
    paths = __import__("macloader.recovery.service", fromlist=["RecoveryService"]).RecoveryService.save_verified_bundle(lock, evidence, tmp_path)
    assert all(path.is_file() for path in paths)
    state = __import__("json").loads((tmp_path / "recovery.state.json").read_text())
    assert state["evidence"]["lock_digest"] == lock.digest
    (tmp_path / "recovery.evidence.json").write_text(
        json.dumps({**evidence.to_dict(), "verified_chunks": 2}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="authoritative state"):
        RecoveryService.load_lock(tmp_path / "recovery.lock.json")
    lock_path = tmp_path / "recovery.lock.json"
    evidence_path = tmp_path / "recovery.evidence.json"
    state_path = tmp_path / "recovery.state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    lock_payload = lock.to_dict()
    evidence_payload = evidence.to_dict()
    lock_changed = {**lock_payload, "state": "locked"}
    lock_path.write_text(json.dumps(lock_changed), encoding="utf-8")
    with pytest.raises(ValueError, match="authoritative state"):
        RecoveryService.load_lock(lock_path)
    lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")
    evidence_path.unlink()
    with pytest.raises(ValueError, match="incomplete"):
        RecoveryService.load_lock(lock_path)
    evidence_path.write_text(json.dumps(evidence_payload), encoding="utf-8")
    state_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        RecoveryService.load_lock(lock_path)
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")
    malformed_evidence = dict(evidence_payload)
    malformed_evidence.pop("verified_chunks")
    evidence_path.write_text(json.dumps(malformed_evidence), encoding="utf-8")
    state_payload["evidence"] = malformed_evidence
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        RecoveryService.load_lock(lock_path)
    mismatched_evidence = dict(evidence_payload)
    mismatched_evidence["lock_digest"] = "b" * 64
    evidence_path.write_text(json.dumps(mismatched_evidence), encoding="utf-8")
    state_payload["evidence"] = mismatched_evidence
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="different lock"):
        RecoveryService.load_lock(lock_path)
    with pytest.raises(ValueError, match="different lock identity"):
        RecoveryService.save_verified_bundle(
            lock, replace(evidence, lock_digest="b" * 64), tmp_path / "bad-publication"
        )


def test_recovery_binding_rejects_stale_dependency_identity() -> None:
    recovery = RecoveryService(load_recovery_policy())
    toolchain = ToolchainSelection("0.1", "1.0.7", "1.0.7", None, None, None, "linux", "x86_64")
    plan = BuildPlan(
        "Lenovo ThinkPad T480s", "sequoia", "snapshot", CompatibilityState.EXPERIMENTAL,
        target_version="15.0", target_build="24A335", stable_model_id="thinkpad-t480s",
    )
    profile = load_reviewed_profile()
    manifest = BuildManifest(
        "0.1", "b" * 64, plan.target_model, plan.target_macos, "c" * 64, "VALID",
        toolchain_digest=toolchain.digest,
        profile_digest=profile.source_digest, evidence_digests=("d" * 64,),
        output_digest="e" * 64, identity_digest="f" * 64,
    )
    binding = recovery.derive_binding(
        UserConfiguration(), plan, toolchain, manifest, recovery.policy.digest,
        expected_artifact_lock_digest="c" * 64,
        expected_build_digest=manifest.build_digest,
        expected_profile_digest=profile.source_digest,
        expected_evidence_digests=("d" * 64,),
    )
    assert binding.efi_manifest_digest == canonical_json_digest(manifest.to_dict())
    product = RecoveryProduct(
        recovery.policy.target, "https://updates.cdn-apple.com/image", "a" * 64, 1,
        "https://updates.cdn-apple.com/chunk", "b" * 64, 1, "<private>", "<private>",
    )
    discovered = RecoveryDiscoveryResult(
        RecoveryState.DISCOVERED, recovery.policy.target, product, "e" * 64,
    )
    verified = {
        "configuration_digest": binding.configuration_digest,
        "build_plan_digest": binding.build_plan_digest,
        "catalog_digest": binding.catalog_digest,
        "toolchain_digest": binding.toolchain_digest,
        "efi_manifest_digest": binding.efi_manifest_digest,
    }
    with pytest.raises(ValueError, match="current verified workflow"):
        recovery.lock(
            discovered, binding, verified_artifacts=verified, require_verified=True
        )
    with pytest.raises(ValueError, match="current verified workflow"):
        recovery.lock(
            discovered,
            replace(binding, catalog_digest="f" * 64),
            verified_artifacts=verified,
            require_verified=True,
        )
    with pytest.raises(ValueError, match="current dependency catalog"):
        recovery.derive_binding(
            UserConfiguration(), plan, toolchain, manifest, "",
            expected_artifact_lock_digest="c" * 64,
            expected_build_digest=manifest.build_digest,
            expected_profile_digest=profile.source_digest,
            expected_evidence_digests=("d" * 64,),
        )
    with pytest.raises(ValueError, match="artifact lock"):
        recovery.derive_binding(
            UserConfiguration(), plan, toolchain, manifest, recovery.policy.digest,
            expected_artifact_lock_digest="9" * 64,
            expected_build_digest=manifest.build_digest,
            expected_profile_digest=profile.source_digest,
            expected_evidence_digests=("d" * 64,),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"target_macos": "sonoma"}, "exact Recovery policy target"),
        ({"validation_report": "STRUCTURAL_ONLY"}, "qualified validated"),
        ({"toolchain_digest": "9" * 64}, "toolchain identity"),
        ({"build_digest": "9" * 64}, "build identity"),
        ({"profile_digest": "9" * 64}, "profile"),
        ({"evidence_digests": ("9" * 64,)}, "evidence"),
        ({"identity_reference": "other.json"}, "identity reference"),
    ],
)
def test_recovery_binding_rejects_foreign_manifest_fields(change: dict[str, object], message: str) -> None:
    recovery = RecoveryService(load_recovery_policy())
    toolchain = ToolchainSelection("0.1", "1.0.7", "1.0.7", None, None, None, "linux", "x86_64")
    plan = BuildPlan(
        "Lenovo ThinkPad T480s", "sequoia", "snapshot", CompatibilityState.EXPERIMENTAL,
        target_version="15.0", target_build="24A335", stable_model_id="thinkpad-t480s",
    )
    profile = load_reviewed_profile()
    manifest = BuildManifest(
        "0.1", "b" * 64, plan.target_model, plan.target_macos, "c" * 64, "VALID",
        toolchain_digest=toolchain.digest, profile_digest=profile.source_digest,
        evidence_digests=("d" * 64,), identity_reference="private.json",
    )
    mutated = replace(manifest, **change)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        recovery.derive_binding(
            UserConfiguration(), plan, toolchain, mutated, recovery.policy.digest,
            expected_artifact_lock_digest="c" * 64,
            expected_build_digest=manifest.build_digest,
            expected_profile_digest=profile.source_digest,
            expected_evidence_digests=("d" * 64,),
            expected_identity_reference="private.json",
        )


def test_efi_builder_rejects_unbound_profile_and_missing_toolchain(tmp_path: Path) -> None:
    db = Database()
    catalog = db.get_dependency_catalog()
    assert catalog is not None
    plan = BuildPlan(
        "Lenovo ThinkPad T480s", "sequoia", "snapshot", CompatibilityState.EXPERIMENTAL,
        policy_version=catalog.policy_version,
    )
    resolved = __import__("macloader.dependencies.resolver", fromlist=["DependencyResolver"]).DependencyResolver(db).resolve(plan)
    builder = EfiBuilder(db=db)
    with pytest.raises(BuildPlanError, match="requires a validated toolchain"):
        builder.build(plan, resolved, {}, tmp_path / "no-toolchain")
    bound_plan = replace(plan, accepted_configuration_digest="a" * 64)
    with pytest.raises(BuildPlanError, match="reviewed EFI profile"):
        builder.build(bound_plan, resolved, {}, tmp_path / "no-profile", toolchain=ToolchainSelection("0.1", "1.0.7", "1.0.7", None, None, None, "linux", "x86_64"))


def test_efi_builder_rejects_unprofiled_production_toolchain(tmp_path: Path) -> None:
    db = Database()
    catalog = db.get_dependency_catalog()
    assert catalog is not None
    plan = BuildPlan(
        "Lenovo ThinkPad T480s", "sequoia", "snapshot", CompatibilityState.EXPERIMENTAL,
        policy_version=catalog.policy_version,
    )
    resolved = __import__("macloader.dependencies.resolver", fromlist=["DependencyResolver"]).DependencyResolver(db).resolve(plan)
    toolchain = ToolchainSelection("0.1", "1.0.7", "1.0.7", None, None, None, "linux", "x86_64")
    with pytest.raises(BuildPlanError, match="synthetic test mode"):
        EfiBuilder(db=db).build(plan, resolved, {}, tmp_path / "production-legacy", toolchain=toolchain)


def test_windows_backend_without_production_attestation_stays_nonqualified() -> None:
    backend = SimpleNamespace(
        lock_and_dismount=lambda *_args: None,
        write=lambda *_args: None,
        flush=lambda *_args: None,
        readback=lambda *_args: True,
        invalidate=lambda *_args: None,
        remount=lambda *_args: None,
        production_qualified=False,
    )
    from macloader.removable.adapters import WindowsRemovableAdapter

    adapter = WindowsRemovableAdapter(backend=backend, platform="win32")
    assert adapter.status.qualified is False
    assert "production media qualification" in adapter.status.reason


def test_efi_validation_cancellation_stops_validator_process(tmp_path: Path) -> None:
    root = tmp_path / "efi"
    for relative in ("EFI/BOOT/BOOTx64.efi", "EFI/OC/OpenCore.efi", "EFI/OC/Drivers/OpenRuntime.efi"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    (root / "EFI/OC/config.plist").write_bytes(
        plistlib.dumps({
            "OC": {"Version": "1.0.7"}, "UEFI": {"Drivers": []}, "Kernel": {"Add": []},
            "PlatformInfo": {"Generic": {
                "SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL",
                "MLB": "MLB1234", "SystemUUID": "12345678",
            }},
        })
    )
    validator = tmp_path / "validator.py"
    validator.write_text("import time; time.sleep(10)", encoding="utf-8")
    validator_digest = hashlib.sha256(validator.read_bytes()).hexdigest()
    toolchain = ToolchainSelection(
        "0.1", "1.0.7", "1.0.7", None, None, None, "linux", "x86_64",
        {"qualification": "qualified"}, str(validator), validator_digest,
    )
    builder = EfiBuilder()
    manifest = BuildManifest(
        "0.1", "a" * 64, "Lenovo ThinkPad T480s", "sequoia", "b" * 64, "VALID",
        output_paths={"efi": "EFI"}, toolchain_digest="c" * 64, identity_digest="d" * 64,
        output_digest=builder._tree_digest(root),
    )
    (root / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    calls = [0]

    def cancel_after_tree() -> bool:
        calls[0] += 1
        return calls[0] > 20

    report = builder.validate_tree(
        root, toolchain=toolchain, expected_manifest=manifest,
        cancel=cancel_after_tree, synthetic_test_mode=True,
    )
    assert report.status == "INVALID"
    assert any("cancelled" in error for error in report.errors)


def test_recovery_lock_requires_exact_verified_artifact_map() -> None:
    policy = load_recovery_policy()
    digest = "a" * 64
    product = RecoveryProduct(
        policy.target, "https://updates.cdn-apple.com/image", digest, 1,
        "https://updates.cdn-apple.com/chunk", digest, 1, "<private>", "<private>",
    )
    binding = RecoveryBinding(
        policy.target.digest, "thinkpad-t480s", digest, digest, policy.digest, digest, digest, digest
    )
    result = RecoveryDiscoveryResult(RecoveryState.DISCOVERED, policy.target, product, digest)
    service = RecoveryService(policy)
    with pytest.raises(ValueError, match="verified artifacts"):
        service.lock(result, binding, require_verified=True)
    verified = {name: getattr(binding, name) for name in (
        "configuration_digest", "build_plan_digest", "catalog_digest", "toolchain_digest", "efi_manifest_digest"
    )}
    with pytest.raises(ValueError, match="current verified workflow"):
        service.lock(result, binding, verified_artifacts=verified, require_verified=True)
    with pytest.raises(ValueError, match="current verified artifacts"):
        service.lock(result, binding, verified_artifacts={**verified, "catalog_digest": "b" * 64})
    assert service.lock(result, binding, verified_artifacts=verified).state == RecoveryState.LOCKED


def test_recovery_verify_requires_published_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy = load_recovery_policy()
    digest = "a" * 64
    product = RecoveryProduct(
        policy.target, "https://updates.cdn-apple.com/image", digest, 1,
        "https://updates.cdn-apple.com/chunk", digest, 1, "<private>", "<private>",
    )
    binding = RecoveryBinding(policy.target.digest, "thinkpad-t480s", digest, digest, policy.digest, digest, digest, digest)
    lock = RecoveryLock("1", RecoveryState.VERIFIED, product, binding, policy.digest, digest)
    evidence = RecoveryEvidence(lock.digest, digest, digest, True, 1, 1, "test")
    RecoveryService.save_verified_bundle(lock, evidence, tmp_path)
    image = tmp_path / "image"
    chunklist = tmp_path / "chunklist"
    image.write_bytes(b"image")
    chunklist.write_bytes(b"chunklist")
    monkeypatch.setattr(
        __import__("macloader.recovery.service", fromlist=["verify_apple_chunklist"]),
        "verify_apple_chunklist",
        lambda *_args, **_kwargs: (1, 1),
    )
    verified = RecoveryService(policy).verify(lock, image, chunklist, cancel=lambda: False)
    assert verified.lock_digest == lock.digest


def test_strict_media_boundary_accepts_one_verified_published_generation_and_rejects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    (source / "EFI" / "BOOT").mkdir(parents=True)
    (source / "EFI" / "OC").mkdir()
    (source / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(b"boot")
    (source / "EFI" / "OC" / "OpenCore.efi").write_bytes(b"opencore")
    (source / "EFI" / "OC" / "config.plist").write_bytes(b"config")
    recovery_dir = source / "Recovery"
    recovery_dir.mkdir()
    policy = load_recovery_policy()
    digest = "a" * 64
    image_bytes = b"image"
    chunklist_bytes = b"chunklist"
    image_digest = hashlib.sha256(image_bytes).hexdigest()
    chunklist_digest = hashlib.sha256(chunklist_bytes).hexdigest()
    (recovery_dir / "Recovery-24A335.dmg").write_bytes(image_bytes)
    (recovery_dir / "Recovery-24A335.chunklist").write_bytes(chunklist_bytes)
    product = RecoveryProduct(
        policy.target,
        "https://updates.cdn-apple.com/image",
        image_digest,
        len(image_bytes),
        "https://updates.cdn-apple.com/chunk",
        chunklist_digest,
        len(chunklist_bytes),
        "<private>",
        "<private>",
    )
    manifest = BuildManifest(
        "0.1", digest, "Lenovo ThinkPad T480s", "sequoia", digest, "VALID",
        output_paths={"efi": "EFI"}, toolchain_digest=digest, identity_digest=digest,
        output_digest=RemovableMediaWriter._efi_payload_digest(source), license_digests={},
        schema_digest=digest, profile_digest=digest, acpi_digest=digest,
        evidence_digests=(digest,), usb_policy_state="", usb_first_install_route="",
        identity_reference="private.json",
    )
    (source / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    binding = RecoveryBinding(
        policy.target.digest, "thinkpad-t480s", digest, digest, policy.digest,
        digest, digest, canonical_json_digest(manifest.to_dict()),
    )
    lock = RecoveryLock("1", RecoveryState.VERIFIED, product, binding, policy.digest, digest)
    evidence = RecoveryEvidence(lock.digest, image_digest, chunklist_digest, True, 1, len(image_bytes), "test")
    RecoveryService.save_verified_bundle(lock, evidence, recovery_dir)
    bindings = MediaBindings.from_published_contracts(manifest, lock, evidence)
    # The fixture is synthetic; production uses the real OpenCore signed
    # chunklist verifier at this exact media boundary.
    monkeypatch.setattr(
        "macloader.removable.writer.verify_apple_chunklist",
        lambda *_args: (1, len(image_bytes)),
    )
    RemovableMediaWriter._validate_published_artifacts(source, bindings, require_all=True)
    with pytest.raises(UnsafeRemovableTarget, match="bindings disagree"):
        RemovableMediaWriter._validate_published_artifacts(
            source, replace(bindings, toolchain_digest="c" * 64), require_all=True
        )
    (recovery_dir / "Recovery-24A335.dmg").write_bytes(b"mutated recovery")
    with pytest.raises(UnsafeRemovableTarget, match="Recovery payload bytes"):
        RemovableMediaWriter._validate_published_artifacts(source, bindings, require_all=True)
    (recovery_dir / "Recovery-24A335.dmg").write_bytes(image_bytes)
    (source / "EFI" / "OC" / "config.plist").write_bytes(b"mutated")
    with pytest.raises(UnsafeRemovableTarget, match="payload contents"):
        RemovableMediaWriter._validate_published_artifacts(source, bindings, require_all=True)
    (source / "EFI" / "OC" / "config.plist").write_bytes(b"config")
    monkeypatch.undo()
    with pytest.raises(UnsafeRemovableTarget, match="signed chunklist"):
        RemovableMediaWriter._validate_published_artifacts(source, bindings, require_all=True)


def test_workflow_identity_reuse_is_an_explicit_filename_reference(tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft = UserConfiguration()
    updated = service.set_identity_reference(draft, "private-identity.json")
    assert updated.identity_ref is not None
    assert updated.identity_ref.storage_ref == "private-identity.json"


def test_direct_write_plan_cannot_bypass_source_validation(tmp_path: Path) -> None:
    device = RemovableDevice("USB-1", "Disposable", 16_000_000_000, False, True, False, serial="SERIAL")
    bindings = MediaBindings(*(["a" * 64] * 6))
    plan = WritePlan(device, 1, bindings=bindings)
    writer = RemovableMediaWriter(destructive_write=lambda *_: pytest.fail("write callback reached"), readback_verifier=lambda *_: True)
    with pytest.raises(UnsafeRemovableTarget, match="source-validation"):
        writer.write(plan, tmp_path, DestructiveConfirmation.issue(plan))


def test_strict_media_boundary_requires_matching_published_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    recovery = source / "Recovery"
    recovery.mkdir(parents=True)
    manifest = {"validation_report": "VALID"}
    (source / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
    lock = {"schema_version": "1", "state": "VERIFIED", "product": {}, "binding": {}, "source_policy_digest": "a" * 64, "discovery_record_digest": "b" * 64}
    lock_identity = dict(lock)
    lock_identity.pop("state")
    lock_digest = canonical_json_digest(lock_identity)
    (recovery / "recovery.lock.json").write_text(__import__("json").dumps(lock), encoding="utf-8")
    (recovery / "recovery.evidence.json").write_text(
        __import__("json").dumps({"lock_digest": lock_digest, "signed_chunklist": True}), encoding="utf-8"
    )
    bindings = MediaBindings(canonical_json_digest(manifest), lock_digest, *(["c" * 64] * 4))
    with pytest.raises(UnsafeRemovableTarget, match="valid published contract"):
        RemovableMediaWriter._validate_published_artifacts(source, bindings, require_all=True)


def test_identity_reuse_rejects_broad_permissions_after_validating_content(tmp_path: Path) -> None:
    service = IdentityService(tmp_path)
    stored = service.store(service.fake_identity())
    path = tmp_path / stored.storage_ref
    path.chmod(0o644)
    with pytest.raises(IdentityServiceError, match="permissions"):
        service.reuse(IdentityReference("0.1", stored.storage_ref, True))


def test_tui_apply_target_is_clickable_at_supported_small_sizes(t480s_baseline_fixture: Path) -> None:
    async def exercise() -> None:
        for size in ((80, 24), (100, 35)):
            app = WorkflowApp(fixture=t480s_baseline_fixture)
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                await pilot.click("#apply-target")
                assert app._draft is not None

    asyncio.run(exercise())
