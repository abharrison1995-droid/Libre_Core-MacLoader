"""P6 shared workflow service parity and persistence tests."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from dataclasses import replace
from typing import Iterator

import pytest

from macloader.configuration.store import ConfigurationStore
from macloader.domain.configuration import UserConfiguration
from macloader.identity.service import IdentityService, IdentityServiceError
from macloader.workflow.service import WorkflowService
from macloader.removable.writer import RemovableDevice
import macloader.workflow.service as workflow_module


def test_workflow_create_set_target_save_and_public_export(t480s_baseline_fixture: Path, tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, snapshot = service.create(t480s_baseline_fixture, sanitize=True)
    assert draft.hardware_snapshot_id == snapshot.snapshot_id
    targeted = service.set_target(draft, "15.0", "24A335")
    selected = service.set_option(targeted, "profile.graphics", "kaby-lake-r-uhd620")
    path = service.save(selected)
    assert path.is_file()
    restored = service.load(selected.configuration_id)
    assert restored.semantic_digest == selected.semantic_digest
    public_path = tmp_path / "public.json"
    service.export_file(restored, public_path)
    exported = json.loads(public_path.read_text(encoding="utf-8"))
    assert exported["identity_ref"] is None
    assert exported["configuration_id"] == selected.configuration_id


def test_workflow_evaluation_is_the_shared_cli_tui_semantic_result(t480s_baseline_fixture: Path, tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, snapshot = service.create(t480s_baseline_fixture)
    state = service.evaluate(draft, snapshot)
    assert json.loads(service.render_json(state)) == state.to_dict()
    assert state.to_dict()["semantic_digest"] == draft.semantic_digest
    assert state.to_dict()["plan"]["hardware_snapshot_id"] == snapshot.snapshot_id


def test_workflow_imports_legacy_plan_as_review_blocked(tmp_path: Path) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({
        "target_model": "Lenovo ThinkPad T480s",
        "target_macos": "sequoia",
        "hardware_snapshot_id": "legacy-snapshot",
    }), encoding="utf-8")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, issues = service.import_file(source)
    assert isinstance(draft, UserConfiguration)
    assert issues[0].code == "LEGACY_PLAN_REQUIRES_REVIEW"
    with pytest.raises(ValueError, match="unreadable"):
        service.import_file(tmp_path / "does-not-exist.json")


def test_workflow_revision_and_acknowledgement_invalidate_on_change(t480s_baseline_fixture: Path, tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, snapshot = service.create(t480s_baseline_fixture)
    option = service.orchestrator.configuration_service.policy.options["profile.smbios"]
    acknowledged = service.acknowledge(draft, option.option_id, option.explanation)
    assert acknowledged.revision == draft.revision + 1
    changed = service.set_option(acknowledged, "profile.audio", "layout-11")
    assert changed.revision == acknowledged.revision + 1
    assert changed.acknowledgements == ()
    assert service.evaluate(changed, snapshot).configuration == changed


def test_workflow_stage_wrappers_keep_media_non_destructive(
    t480s_baseline_fixture: Path, tmp_path: Path
) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, snapshot = service.create(t480s_baseline_fixture)
    assert service.plan(draft, snapshot).hardware_snapshot_id == snapshot.snapshot_id
    state, dependencies = service.resolve_dependencies(draft, snapshot)
    assert state.configuration == draft
    assert dependencies.policy_version
    with pytest.raises(ValueError, match="EFI build blocked"):
        service.build_efi_preview(draft, snapshot, tmp_path / "efi")
    media = service.preview_media_plan(
        RemovableDevice("USB-EXAMPLE", "Disposable USB", 16_000_000_000, False, True, False),
        1_000_000_000,
    )
    assert media.to_dict()["destructive_write_enabled"] is False


def test_shared_preflight_reports_host_media_discovery_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))

    def failed_discovery() -> dict[str, object]:
        raise OSError("synthetic lsblk failure")

    monkeypatch.setattr(WorkflowService, "removable_status", staticmethod(failed_discovery))
    report = service.preflight(None, None)
    media_check = next(item for item in report["checks"] if item["id"] == "physical_media")

    assert media_check["state"] == "blocked"
    assert "OSError" in media_check["action"]
    assert len(report["checks"]) >= 8


def test_preflight_verifies_synthetic_machine_evidence_and_private_identity(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workflow_module, "DEFAULT_PRIVATE_DIR", tmp_path / "private")
    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", tmp_path / "private" / "acpi")
    monkeypatch.setattr(workflow_module, "DEFAULT_IDENTITY_DIR", tmp_path / "private" / "identities")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, original_snapshot = service.create(t480s_baseline_fixture)
    configuration = service.set_target(configuration, "15.0", "24A335")
    snapshot = replace(original_snapshot, machine_type="20L8", bios_version="N22ET85W (1.62 )")
    synthetic_capture = _synthetic_acpi_capture(tmp_path)
    configuration, _record = service.import_acpi_capture(configuration, snapshot, synthetic_capture)
    identity = IdentityService(tmp_path / "private" / "identities").store(IdentityService.fake_identity())
    configuration = service.set_identity_reference(configuration, identity.storage_ref)

    from macloader.toolchain.loader import TrustedToolchainLoader

    monkeypatch.setattr(TrustedToolchainLoader, "select", lambda _self: object())
    monkeypatch.setattr(service, "resolve_dependencies", lambda _configuration, _snapshot: (None, type("Deps", (), {"is_complete": True})()))
    monkeypatch.setattr(service.orchestrator, "verify_cached_dependencies", lambda _dependencies, *, plan: {"synthetic": True})
    monkeypatch.setattr(WorkflowService, "removable_status", staticmethod(lambda: {"status": "discovery_not_qualified"}))

    report = service.preflight(configuration, snapshot)
    checks = {item["id"]: item for item in report["checks"]}

    assert checks["exact_target"]["state"] == "ready"
    assert checks["reference_machine"]["state"] == "ready"
    assert checks["private_acpi"]["state"] == "ready"
    assert checks["private_identity"]["state"] == "ready"
    assert checks["toolchain"]["state"] == "ready"
    assert checks["dependencies"]["state"] == "ready"
    # No discovery evidence is recorded in this isolated workspace; the gate
    # is derived from current evidence, not a fixed historical message.
    assert checks["exact_recovery"]["state"] == "missing"
    assert "macloader recovery resolve" in checks["exact_recovery"]["action"]
    assert checks["physical_media"]["state"] == "unqualified"


def test_real_identity_generation_requires_exact_checkpoint_before_tool_acquisition(
    t480s_baseline_fixture: Path, tmp_path: Path
) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, _snapshot = service.create(t480s_baseline_fixture)
    with pytest.raises(IdentityServiceError, match="exact explicit confirmation"):
        service.generate_private_identity(configuration, "not the checkpoint")


@pytest.mark.parametrize(("offline", "next_action"), [(True, "toolchain install"), (False, "network access")])
def test_efi_preview_gives_actionable_toolchain_acquisition_error(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    offline: bool, next_action: str,
) -> None:
    from types import SimpleNamespace
    from macloader.toolchain.loader import ToolchainTrustError, TrustedToolchainLoader

    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(t480s_baseline_fixture)
    state = SimpleNamespace(evaluation=SimpleNamespace(has_blockers=False, plan=object()))
    dependencies = SimpleNamespace(is_complete=True, unresolved_requirements=())
    monkeypatch.setattr(service, "resolve_dependencies", lambda *_args, **_kwargs: (state, dependencies))

    def fail_acquisition(_self: TrustedToolchainLoader) -> object:
        raise ToolchainTrustError("synthetic unavailable pinned toolchain")

    monkeypatch.setattr(TrustedToolchainLoader, "select", fail_acquisition)
    monkeypatch.setattr(TrustedToolchainLoader, "provision", fail_acquisition)
    with pytest.raises(ValueError, match=next_action):
        service.build_efi_preview(configuration, snapshot, tmp_path / "efi", offline=offline)


def test_efi_preview_reports_missing_private_acpi_before_dependency_fetch(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from macloader.toolchain.loader import TrustedToolchainLoader

    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(t480s_baseline_fixture)
    state = SimpleNamespace(evaluation=SimpleNamespace(has_blockers=False, plan=object()))
    dependencies = SimpleNamespace(is_complete=True, unresolved_requirements=())
    monkeypatch.setattr(service, "resolve_dependencies", lambda *_args, **_kwargs: (state, dependencies))
    monkeypatch.setattr(TrustedToolchainLoader, "select", lambda _self: object())

    with pytest.raises(ValueError, match="machine-bound ACPI evidence"):
        service.build_efi_preview(configuration, snapshot, tmp_path / "efi", offline=True)


def test_synthetic_workflow_reaches_efi_builder_only_after_private_acpi_binding(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from macloader.toolchain.loader import TrustedToolchainLoader

    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", tmp_path / "private" / "acpi")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, original_snapshot = service.create(t480s_baseline_fixture)
    snapshot = replace(original_snapshot, machine_type="20L8", bios_version="N22ET85W-1.62")
    configuration, evidence = service.import_acpi_capture(
        configuration, snapshot, _synthetic_acpi_capture(tmp_path)
    )
    configuration = service.set_target(configuration, "15.0", "24A335")
    plan = object()
    dependencies = SimpleNamespace(is_complete=True)
    state = SimpleNamespace(evaluation=SimpleNamespace(has_blockers=False, plan=plan))
    selection = object()
    monkeypatch.setattr(service, "resolve_dependencies", lambda *_args, **_kwargs: (state, dependencies))
    monkeypatch.setattr(TrustedToolchainLoader, "provision", lambda _self: selection)
    monkeypatch.setattr(service.orchestrator, "fetch_dependencies", lambda *_args, **_kwargs: {"synthetic": Path("verified.zip")})
    captured: dict[str, object] = {}

    @contextmanager
    def lease(_dependencies: object, **_kwargs: object) -> Iterator[dict[str, Path]]:
        yield {"synthetic": Path("verified.zip")}

    def build(*args: object, **kwargs: object) -> object:
        captured["args"] = args
        captured.update(kwargs)
        return SimpleNamespace(validation=SimpleNamespace(status="VALID"))

    monkeypatch.setattr(service.orchestrator, "lease_dependencies", lease)
    monkeypatch.setattr(service.orchestrator, "build_efi", build)

    result = service.build_efi_preview(configuration, snapshot, tmp_path / "efi", offline=False)

    assert result.validation.status == "VALID"
    assert captured["reviewed_profile"] is not None
    assert captured["private_acpi_capture"] == Path(evidence.private_ref).parent
    assert captured["toolchain"] is selection


def test_preflight_reports_configuration_snapshot_and_dependency_blockers(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(t480s_baseline_fixture)
    monkeypatch.setattr(service, "resolve_dependencies", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic resolver failure")))
    monkeypatch.setattr(WorkflowService, "removable_status", staticmethod(lambda: {"status": "discovery_not_qualified"}))

    report = service.preflight(configuration, replace(snapshot, snapshot_id="different-snapshot"))
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["exact_target"]["state"] == "missing"
    assert checks["machine_snapshot"]["state"] == "blocked"
    assert checks["dependencies"]["state"] == "missing"

    matching_report = service.preflight(configuration, snapshot)
    matching = {item["id"]: item for item in matching_report["checks"]}
    assert matching["dependencies"]["state"] == "blocked"
    assert "RuntimeError" in matching["dependencies"]["action"]


def test_private_resume_snapshot_round_trip_is_owner_only(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workflow_module, "DEFAULT_PRIVATE_DIR", tmp_path / "private")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(t480s_baseline_fixture)
    service.save(configuration)
    path = service.save_snapshot(configuration, snapshot)
    restored = service.resume_snapshot(configuration.configuration_id)

    assert restored.snapshot_id == snapshot.snapshot_id
    assert restored.to_dict() == snapshot.to_dict()
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def _synthetic_acpi_capture(root: Path) -> Path:
    from macloader.build.acpi import TABLE_NAMES

    capture = root / "capture"
    capture.mkdir(parents=True)
    for name in TABLE_NAMES:
        table = bytearray(36)
        table[:4] = b"DSDT" if name == "dsdt.dat" else b"SSDT"
        table[4:8] = (36).to_bytes(4, "little")
        table[8] = 2
        table[10:16] = b"TEST  "
        table[16:24] = b"SYNTHETC"
        table[9] = (-sum(table)) % 256
        (capture / name).write_bytes(table)
    return capture


def test_machine_bound_acpi_import_is_private_and_public_export_redacts_paths(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workflow_module, "DEFAULT_PRIVATE_DIR", tmp_path / "private")
    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", tmp_path / "private" / "acpi")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, original_snapshot = service.create(t480s_baseline_fixture)
    snapshot = replace(original_snapshot, machine_type="20L8", bios_version="N22ET85W (1.62 )")
    service.save(configuration)
    capture = _synthetic_acpi_capture(tmp_path)

    updated, record = service.import_acpi_capture(configuration, snapshot, capture)
    metadata_path = Path(record.private_ref)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    public_path = tmp_path / "public.json"
    service.export_file(updated, public_path)
    public_text = public_path.read_text(encoding="utf-8")

    assert record.completeness.value == "complete"
    assert len(metadata["tables"]) == 12
    assert "PRIVATE-ACPI" not in public_text
    assert str(tmp_path / "private") not in public_text
    if os.name != "nt":
        assert metadata_path.stat().st_mode & 0o077 == 0
        assert all(path.stat().st_mode & 0o077 == 0 for path in (metadata_path.parent / "PRIVATE-ACPI").iterdir())


def test_machine_bound_acpi_import_rejects_wrong_bios_or_corrupt_capture(
    t480s_baseline_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", tmp_path / "private" / "acpi")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(t480s_baseline_fixture)
    capture = _synthetic_acpi_capture(tmp_path)

    reference_snapshot = replace(snapshot, machine_type="20L8")
    with pytest.raises(ValueError, match="BIOS 1.62"):
        service.import_acpi_capture(configuration, reference_snapshot, capture)

    exact_snapshot = replace(reference_snapshot, bios_version="N22ET85W-1.62")
    corrupt_table = capture / "ssdt1.dat"
    corrupt_table.write_bytes(b"bad")
    with pytest.raises(Exception, match="truncated"):
        service.import_acpi_capture(configuration, exact_snapshot, capture)
