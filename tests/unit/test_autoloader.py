"""Guided session orchestration uses existing configuration and safe actions."""
from dataclasses import replace
import json
from pathlib import Path

from macloader.autoloader.models import ActionKind, Stage
from macloader.autoloader.service import AutoloaderService
from macloader.domain.hardware import HardwareSnapshot


FIXTURE = Path(__file__).parents[1] / "fixtures" / "t480s" / "t480s_20l8_162_synthetic.json"


def candidate() -> HardwareSnapshot:
    return HardwareSnapshot.from_dict(json.loads(FIXTURE.read_text()))


def test_exact_candidate_creates_and_resumes_without_configuration_id(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path / "private")
    action = service.start(candidate(), private_material="synthetic-private-machine")
    assert action.stage == Stage.ACPI
    assert service.session is not None
    identity = service.session.session_id
    restarted = AutoloaderService(root=tmp_path / "private")
    assert restarted.start(candidate(), private_material="synthetic-private-machine").stage == Stage.ACPI
    assert restarted.session is not None and restarted.session.session_id == identity
    assert restarted.configuration is not None and restarted.configuration.target is not None
    assert restarted.configuration.target.build == "24A335"
    status = json.dumps(restarted.public_status())
    assert identity not in status and "machine_binding" not in status
    assert "synthetic-private-machine" not in status and str(tmp_path) not in status


def test_wrong_machine_wrong_bios_or_component_stops(tmp_path: Path) -> None:
    for index, change in enumerate(({"machine_type": "20L7"}, {"bios_version": "wrong"}, {"wifi": []})):
        service = AutoloaderService(root=tmp_path / str(index))
        action = service.start(replace(candidate(), **change), private_material="test")  # type: ignore[arg-type]
        assert action.kind == ActionKind.BLOCKED
        assert action.stage in {Stage.MATCH, Stage.HARDWARE}
        assert action.stage != Stage.BUILD


def test_unknown_panel_is_focused_hardware_blocker(tmp_path: Path) -> None:
    snapshot = candidate()
    snapshot.displays[0].touch_capability = None
    service = AutoloaderService(root=tmp_path)
    assert service.start(snapshot, private_material="test").code == "PANEL_TOUCH_UNKNOWN"
    assert service.match is not None and service.match.unknown == ("panel.touch",)


def test_failed_safe_action_is_journaled_and_retry_resumes(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")

    def fail() -> None:
        raise OSError("private-path-should-not-leak")

    service.handlers[Stage.ACPI] = fail
    result = service.advance_until_blocked()
    assert result.code == "AUTOMATIC_ACTION_FAILED"
    assert "private-path" not in result.message
    assert service.session is not None and service.session.actions[-1]["state"] == "failed"
    restarted = AutoloaderService(root=tmp_path)
    assert restarted.start(candidate(), private_material="test").stage == Stage.ACPI
    assert restarted.session is not None and restarted.session.actions[-1]["state"] == "failed"


def test_cancellation_and_no_progress_do_not_forge_output(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    service.handlers[Stage.ACPI] = lambda: None
    assert service.advance_until_blocked(cancel=lambda: True).code == "CANCELLED"
    assert service.advance_until_blocked().code == "NO_PROGRESS"
    assert service.session is not None and not service.session.artifacts


def test_multiple_or_changed_machine_binding_does_not_resume(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="one-machine")
    first = service.session
    service.start(candidate(), private_material="another-machine")
    assert first is not None and service.session is not None
    assert first.session_id != service.session.session_id


def test_changed_hardware_invalidates_artifact_without_losing_session(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    assert service.session is not None and service.configuration is not None
    session_id = service.session.session_id
    service.session.artifacts["efi"] = {"input_digest": service.configuration.semantic_digest}
    service._save_session()
    changed = candidate()
    changed.storage[0].model = "different-storage"
    service.start(changed, private_material="test")
    assert service.session is not None and service.session.session_id == session_id
    assert "efi" not in service.session.artifacts


def test_started_action_is_marked_interrupted_on_restart(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    assert service.session is not None
    service.session.actions.append({"stage": "acpi", "state": "started"})
    service._save_session()
    restarted = AutoloaderService(root=tmp_path)
    restarted.start(candidate(), private_material="test")
    assert restarted.session is not None and restarted.session.actions[-1]["state"] == "interrupted"
