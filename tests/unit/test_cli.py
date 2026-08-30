"""Unit tests for the Click CLI interface."""

import json
from pathlib import Path
from click.testing import CliRunner

from macloader.ui.cli import cli


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Libre_Core MacLoader" in result.output
    assert "probe" in result.output
    assert "support" in result.output
    assert "plan" in result.output


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
    data = json.loads(result.output)
    assert "snapshot_id" in data
    assert "manufacturer" in data
