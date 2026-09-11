"""P6 shared workflow service parity and persistence tests."""

import json
from pathlib import Path

import pytest

from macloader.configuration.store import ConfigurationStore
from macloader.domain.configuration import UserConfiguration
from macloader.workflow.service import WorkflowService
from macloader.removable.writer import RemovableDevice


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
