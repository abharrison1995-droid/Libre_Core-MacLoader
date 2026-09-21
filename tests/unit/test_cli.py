"""Unit tests for the Click CLI interface."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import pytest
from click.testing import CliRunner

from macloader.ui.cli import cli
from macloader.domain.recovery import RecoveryState, RecoveryTarget
from macloader.recovery.discovery import RecoveryDiscoveryResult
from macloader.orchestrator import Orchestrator
from macloader.exceptions import BuildPlanError
import macloader.workflow.service as workflow_service_module
from macloader.evidence.usb import UsbEvidenceSession, UsbPortObservation


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Libre_Core MacLoader" in result.output
    assert "probe" in result.output
    assert "support" in result.output
    assert "plan" in result.output
    assert "config" in result.output
    assert "evidence" in result.output
    assert "tui" in result.output


def test_cli_probe_with_fixture(t480s_baseline_fixture: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["probe", "--fixture", str(t480s_baseline_fixture)])
    assert result.exit_code == 0
    assert "Hardware Snapshot: 20L7CTO1WW" in result.output
    assert "Intel UHD Graphics 620" in result.output


def test_cli_probe_json_output(t480s_baseline_fixture: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["probe", "--fixture", str(t480s_baseline_fixture), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["manufacturer"] == "LENOVO"
    assert data["product_name"] == "20L7CTO1WW"
    assert data["machine_type"] == "20L7"


def test_cli_probe_save_output(tmp_path: Path, t480s_baseline_fixture: Path) -> None:
    out_file = tmp_path / "saved_probe.json"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["probe", "--fixture", str(t480s_baseline_fixture), "--output", str(out_file)],
    )
    assert result.exit_code == 0
    assert out_file.is_file()
    data = json.loads(out_file.read_text())
    assert data["machine_type"] == "20L7"


def test_cli_support_cmd(t480s_baseline_fixture: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["support", "--fixture", str(t480s_baseline_fixture), "--macos", "sequoia"],
    )
    assert result.exit_code == 0
    assert "Compatibility Report for Lenovo ThinkPad T480s on macOS Sequoia" in result.output


def test_cli_support_cmd_json(t480s_baseline_fixture: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["support", "--fixture", str(t480s_baseline_fixture), "--macos", "tahoe", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["target_macos"] == "tahoe"
    assert data["model_id"] == "thinkpad-t480s"


def test_cli_plan_cmd(t480s_baseline_fixture: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["plan", "--fixture", str(t480s_baseline_fixture), "--macos", "tahoe"],
    )
    assert result.exit_code == 0
    assert "Preliminary EFI BuildPlan: Lenovo ThinkPad T480s (Tahoe)" in result.output
    assert "accelerated_intel_uhd_620" in result.output


def test_cli_plan_cmd_json(t480_mx150_fixture: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["plan", "--fixture", str(t480_mx150_fixture), "--macos", "sequoia", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["target_model"] == "Lenovo ThinkPad T480"
    assert "disable_discrete_gpu" in data["required_capabilities"]


def test_cli_live_probe_runs_without_error() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["probe", "--json"])
    assert result.exit_code == 0
    # Click keeps stderr separate on hosted Windows, where the live probe may
    # emit a redacted timeout warning while still returning a valid snapshot.
    data = json.loads(result.stdout)
    assert "snapshot_id" in data
    assert "manufacturer" in data


def test_cli_recovery_list_is_offline_and_exact() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["recovery", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["target"]["version"] == "15.0"
    assert data["target"]["build"] == "24A335"
    assert data["authentication"] == "apple-signed-chunklist"


def test_cli_recovery_resolve_does_not_claim_unavailable_target(monkeypatch: pytest.MonkeyPatch) -> None:
    result = RecoveryDiscoveryResult(
        RecoveryState.AMBIGUOUS,
        RecoveryTarget("sequoia", "macOS Sequoia", "15.0", "24A335"),
        None,
        "a" * 64,
        ("Apple returned no exact build",),
    )
    monkeypatch.setattr(Orchestrator, "discover_recovery", lambda _self: result)
    runner = CliRunner()
    invoked = runner.invoke(cli, ["recovery", "resolve", "--json"])
    assert invoked.exit_code != 0
    assert "no fallback" in invoked.output


def test_cli_recovery_download_requires_explicit_large_acquisition_checkpoint(tmp_path: Path) -> None:
    binding = tmp_path / "binding.json"
    binding.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["recovery", "download", "--binding", str(binding), "--destination", str(tmp_path / "cache")],
    )
    assert result.exit_code != 0
    assert "explicit checkpoint approval" in result.output


def test_cli_recovery_verify_rejects_untrusted_lock(tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    image = tmp_path / "image"
    chunklist = tmp_path / "chunklist"
    lock.write_text("{}", encoding="utf-8")
    image.write_bytes(b"image")
    chunklist.write_bytes(b"chunklist")
    result = CliRunner().invoke(
        cli,
        ["recovery", "verify", "--lock", str(lock), "--image", str(image), "--chunklist", str(chunklist)],
    )
    assert result.exit_code != 0
    assert "Recovery lock" in result.output


@pytest.mark.parametrize("state", [RecoveryState.UNAVAILABLE, RecoveryState.AMBIGUOUS, RecoveryState.FAILED])
def test_cli_recovery_resolve_never_falls_back_for_terminal_state(
    monkeypatch: pytest.MonkeyPatch, state: RecoveryState
) -> None:
    result = RecoveryDiscoveryResult(
        state,
        RecoveryTarget("sequoia", "macOS Sequoia", "15.0", "24A335"),
        None,
        "d" * 64,
        ("redacted diagnostic",),
    )
    monkeypatch.setattr(Orchestrator, "discover_recovery", lambda _self: result)
    invoked = CliRunner().invoke(cli, ["recovery", "resolve", "--json"])
    assert invoked.exit_code != 0
    assert "no fallback" in invoked.output
    assert "session=" not in invoked.output


def test_cli_config_new_and_check_use_shared_workflow_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, t480s_baseline_fixture: Path
) -> None:
    monkeypatch.setattr(workflow_service_module, "DEFAULT_WORKSPACE_DIR", tmp_path / "workspace")
    created = CliRunner().invoke(cli, ["config", "new", "--fixture", str(t480s_baseline_fixture), "--json"])
    assert created.exit_code == 0, created.output
    data = json.loads(created.output)
    configuration_id = data["configuration"]["configuration_id"]
    checked = CliRunner().invoke(
        cli,
        ["config", "check", configuration_id, "--fixture", str(t480s_baseline_fixture), "--json"],
    )
    assert checked.exit_code == 0, checked.output
    checked_data = json.loads(checked.output)
    assert checked_data["configuration"]["configuration_id"] == configuration_id
    assert checked_data["plan"]["hardware_snapshot_id"] == data["snapshot"]["snapshot_id"]


def test_cli_build_from_configuration_uses_shared_efi_preparation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, t480s_baseline_fixture: Path
) -> None:
    monkeypatch.setattr(workflow_service_module, "DEFAULT_WORKSPACE_DIR", tmp_path / "workspace")
    created = CliRunner().invoke(cli, ["config", "new", "--fixture", str(t480s_baseline_fixture), "--json"])
    assert created.exit_code == 0, created.output
    configuration_id = json.loads(created.output)["configuration"]["configuration_id"]
    captured: dict[str, Any] = {}

    def fake_build(
        self: workflow_service_module.WorkflowService,
        configuration: Any,
        snapshot: Any,
        output: Path,
        **kwargs: Any,
    ) -> Any:
        captured.update({"configuration": configuration, "snapshot": snapshot, "output": output, **kwargs})
        return SimpleNamespace(
            validation=SimpleNamespace(status="STRUCTURAL_ONLY"),
            output_dir=Path(output),
            manifest=SimpleNamespace(to_dict=lambda: {"source": "shared-workflow"}),
        )

    monkeypatch.setattr(workflow_service_module.WorkflowService, "build_efi_preview", fake_build)
    output = tmp_path / "efi"
    result = CliRunner().invoke(
        cli,
        [
            "build", "--fixture", str(t480s_baseline_fixture), "--config", configuration_id,
            "--output", str(output), "--offline",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["configuration"].configuration_id == configuration_id
    assert captured["output"] == output
    assert captured["offline"] is True


def test_cli_build_requires_persisted_reviewed_configuration(
    t480s_baseline_fixture: Path, tmp_path: Path
) -> None:
    result = CliRunner().invoke(
        cli,
        ["build", "--fixture", str(t480s_baseline_fixture), "--output", str(tmp_path / "efi")],
    )
    assert result.exit_code != 0
    assert "persisted reviewed configuration" in result.output


def test_cli_build_reports_shared_workflow_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, t480s_baseline_fixture: Path
) -> None:
    monkeypatch.setattr(workflow_service_module, "DEFAULT_WORKSPACE_DIR", tmp_path / "workspace")
    created = CliRunner().invoke(cli, ["config", "new", "--fixture", str(t480s_baseline_fixture), "--json"])
    configuration_id = json.loads(created.output)["configuration"]["configuration_id"]

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise BuildPlanError("trusted toolchain is unavailable")

    monkeypatch.setattr(workflow_service_module.WorkflowService, "build_efi_preview", blocked)
    result = CliRunner().invoke(
        cli,
        [
            "build", "--fixture", str(t480s_baseline_fixture), "--config", configuration_id,
            "--output", str(tmp_path / "efi"),
        ],
    )
    assert result.exit_code != 0
    assert "Build Error" in result.output
    assert "trusted toolchain is unavailable" in result.output


def test_cli_config_set_ack_export_import_migrate_and_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, t480s_baseline_fixture: Path
) -> None:
    monkeypatch.setattr(workflow_service_module, "DEFAULT_WORKSPACE_DIR", tmp_path / "workspace")
    runner = CliRunner()
    created = runner.invoke(cli, ["config", "new", "--fixture", str(t480s_baseline_fixture), "--json"])
    configuration_id = json.loads(created.output)["configuration"]["configuration_id"]

    changed = runner.invoke(
        cli,
        ["config", "set", configuration_id, "--version", "15.0", "--build", "24A335", "--option", "profile.audio=layout-11", "--json"],
    )
    assert changed.exit_code == 0, changed.output
    acknowledged = runner.invoke(
        cli,
        ["config", "acknowledge", configuration_id, "--rule", "profile.smbios", "--warning", "experimental identity", "--json"],
    )
    assert acknowledged.exit_code == 0, acknowledged.output

    exported_path = tmp_path / "public.json"
    exported = runner.invoke(cli, ["config", "export", configuration_id, str(exported_path)])
    assert exported.exit_code == 0, exported.output
    imported = runner.invoke(cli, ["config", "import", str(exported_path), "--json"])
    assert imported.exit_code == 0, imported.output

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps({"target_model": "Lenovo ThinkPad T480s", "target_macos": "sequoia", "hardware_snapshot_id": "legacy"}), encoding="utf-8")
    migrated = runner.invoke(cli, ["config", "migrate", str(legacy_path), "--json"])
    assert migrated.exit_code == 0, migrated.output
    assert "LEGACY_PLAN_REQUIRES_REVIEW" in migrated.output

    usb_path = tmp_path / "usb.json"
    usb_path.write_text(json.dumps(UsbEvidenceSession(
        "snapshot", "N22ET85W", "private/usb.json", "collector-1",
        (UsbPortObservation("left-a", "HS01", "USB-A", "5Gbps", "8086:9d2f"),),
    ).to_dict()), encoding="utf-8")
    evidence = runner.invoke(cli, ["evidence", "import", configuration_id, str(usb_path), "--kind", "usb", "--json"])
    assert evidence.exit_code == 0, evidence.output

    listed = runner.invoke(cli, ["usb", "list", "--json"])
    assert listed.exit_code == 0
    assert json.loads(listed.output)["writes_enabled"] is False
    planned = runner.invoke(cli, [
        "usb", "plan", "--device-id", "USB-1", "--model", "Disposable", "--capacity-bytes", "16000000000",
        "--serial", "SERIAL-1", "--required-bytes", "1000", "--json",
    ])
    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.output)["destructive_write_enabled"] is False
