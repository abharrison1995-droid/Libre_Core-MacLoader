"""Guided session orchestration uses existing configuration and safe actions."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

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


class _WriteReached(Exception):
    pass


def _write_gate(tmp_path: Path, write_time: HardwareSnapshot) -> None:
    """Run the write-time hardware gate against ``write_time``; the media write itself is faked."""
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    assert service.session is not None and service.snapshot is not None
    bound = replace(write_time, snapshot_id=service.snapshot.snapshot_id)
    service._current_efi_manifest = lambda: None  # type: ignore[assignment,method-assign,return-value]
    service._current_bound_snapshot = lambda: bound  # type: ignore[method-assign]

    def write(*_args: object) -> None:
        raise _WriteReached()

    service.media.write = write  # type: ignore[method-assign,assignment]
    service._write_media()


def _assert_write_allowed(tmp_path: Path, write_time: HardwareSnapshot) -> None:
    import pytest
    with pytest.raises(_WriteReached):
        _write_gate(tmp_path, write_time)


def _assert_hardware_changed(tmp_path: Path, write_time: HardwareSnapshot) -> None:
    import pytest
    from macloader.evidence.acpi_capture import CaptureError
    with pytest.raises(CaptureError) as error:
        _write_gate(tmp_path, write_time)
    assert error.value.code == "HARDWARE_CHANGED"


def test_write_gate_allows_extra_usb_keyboard(tmp_path: Path) -> None:
    from macloader.domain.hardware import InputDeviceInfo
    changed = candidate()
    changed.input_devices.append(InputDeviceInfo(name="Example USB Keyboard", bus="usb", kind="keyboard", vendor_id="1234", product_id="0001"))
    _assert_write_allowed(tmp_path, changed)


def test_write_gate_allows_extra_usb_device(tmp_path: Path) -> None:
    from macloader.domain.hardware import UsbDevice
    changed = candidate()
    changed.usb_devices.append(UsbDevice(vendor_id="1234", product_id="5678", bus="001", device_num="009", product_name="Example Flash"))
    _assert_write_allowed(tmp_path, changed)


def test_write_gate_refuses_changed_bios_version(tmp_path: Path) -> None:
    _assert_hardware_changed(tmp_path, replace(candidate(), bios_version="N22ET99W (1.99 )"))


def test_write_gate_refuses_changed_wifi_card(tmp_path: Path) -> None:
    changed = candidate()
    assert changed.wifi[0].pci is not None
    changed.wifi[0].pci.device_id = "abcd"
    _assert_hardware_changed(tmp_path, changed)


def _assert_resume_keeps_artifacts(tmp_path: Path, relaunch: HardwareSnapshot) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    assert service.session is not None
    for kind in ("tools", "efi", "recovery"):
        service._record_artifact(kind, digest=f"synthetic-{kind}")
    restarted = AutoloaderService(root=tmp_path)
    restarted.start(relaunch, private_material="test")
    assert restarted.session is not None and restarted.session.session_id == service.session.session_id
    assert {"tools", "efi", "recovery"} <= set(restarted.session.artifacts)


def test_resume_keeps_artifacts_with_identical_snapshot(tmp_path: Path) -> None:
    _assert_resume_keeps_artifacts(tmp_path, candidate())


def test_resume_keeps_artifacts_with_extra_usb_device(tmp_path: Path) -> None:
    from macloader.domain.hardware import UsbDevice
    changed = candidate()
    changed.usb_devices.append(UsbDevice(vendor_id="1234", product_id="5678", bus="001", device_num="009", product_name="Example Flash"))
    _assert_resume_keeps_artifacts(tmp_path, changed)


@pytest.mark.xfail(strict=True, reason="external USB input devices change input.topology; not fixed before first install")
def test_resume_keeps_artifacts_with_extra_usb_keyboard(tmp_path: Path) -> None:
    from macloader.domain.hardware import InputDeviceInfo
    changed = candidate()
    changed.input_devices.append(InputDeviceInfo(name="Example USB Keyboard", bus="usb", kind="keyboard", vendor_id="1234", product_id="0001"))
    _assert_resume_keeps_artifacts(tmp_path, changed)


def test_started_action_is_marked_interrupted_on_restart(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    assert service.session is not None
    service.session.actions.append({"stage": "acpi", "state": "started"})
    service._save_session()
    restarted = AutoloaderService(root=tmp_path)
    restarted.start(candidate(), private_material="test")
    assert restarted.session is not None and restarted.session.actions[-1]["state"] == "interrupted"


def _fail_next_save(service: AutoloaderService, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make the store's next save raise once; return the revisions it was handed."""
    original = service.store.save
    seen: list[int] = []

    def flaky(session, expected_revision):  # type: ignore[no-untyped-def]
        seen.append(session.revision)
        monkeypatch.setattr(service.store, "save", original)
        raise OSError("simulated write failure")

    monkeypatch.setattr(service.store, "save", flaky)
    return seen


def test_failed_session_save_restores_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    assert service.session is not None
    previous = service.session.revision
    seen = _fail_next_save(service, monkeypatch)
    with pytest.raises(OSError, match="simulated"):
        service._save_session()
    assert seen == [previous + 1]
    assert service.session.revision == previous
    service._save_session()
    assert service.session.revision == previous + 1
    assert service.store.load(service.session.session_id).revision == previous + 1


def test_failed_resume_save_restores_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    AutoloaderService(root=tmp_path).start(candidate(), private_material="test")
    restarted = AutoloaderService(root=tmp_path)
    captured = []

    def flaky(session, expected_revision):  # type: ignore[no-untyped-def]
        captured.append((session, expected_revision))
        raise OSError("simulated write failure")

    monkeypatch.setattr(restarted.store, "save", flaky)
    with pytest.raises(OSError, match="simulated"):
        restarted.start(candidate(), private_material="test")
    (session, expected), = captured
    assert session.revision == expected


def test_normal_session_save_increments_revision_by_one(tmp_path: Path) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    assert service.session is not None
    before = service.session.revision
    service._save_session()
    assert service.session.revision == before + 1
    assert service.store.load(service.session.session_id).revision == before + 1


def test_conflicting_writer_is_not_overwritten_after_failed_save(tmp_path: Path) -> None:
    AutoloaderService(root=tmp_path).start(candidate(), private_material="test")
    writer_a = AutoloaderService(root=tmp_path)
    writer_a.start(candidate(), private_material="test")
    writer_b = AutoloaderService(root=tmp_path)
    writer_b.start(candidate(), private_material="test")
    assert writer_a.session is not None and writer_b.session is not None
    writer_b.session.actions.append({"stage": "acpi", "state": "writer-b-marker"})
    writer_b._save_session()
    stored_b = writer_b.store.load(writer_b.session.session_id)
    stale = writer_a.session.revision
    writer_a.session.actions.append({"stage": "acpi", "state": "writer-a-marker"})
    with pytest.raises(ValueError, match="changed"):
        writer_a._save_session()
    assert writer_a.session.revision == stale
    # Without the rollback each failed attempt advances the stale revision
    # until one lines up with the stored file and overwrites writer B.
    for _ in range(3):
        with pytest.raises(ValueError, match="changed"):
            writer_a._save_session()
        assert writer_a.session.revision == stale
    assert writer_a.store.load(writer_a.session.session_id) == stored_b
