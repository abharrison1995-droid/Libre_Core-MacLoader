"""Unit tests for the CLI deps subcommands (list, resolve, fetch, verify, cache)."""

import json
from pathlib import Path
from click.testing import CliRunner

from macloader.ui.cli import cli


def test_cli_deps_list() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["deps", "list"])
    assert result.exit_code == 0
    assert "OpenCorePkg" in result.output
    assert "Lilu" in result.output
    assert "WhateverGreen" in result.output


def test_cli_deps_list_json() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["deps", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["policy_version"] == "2026.08-a"
    assert len(data["dependencies"]) >= 10


def test_cli_deps_resolve_with_fixture(t480s_baseline_fixture: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["deps", "resolve", "-f", str(t480s_baseline_fixture), "-m", "sequoia"],
    )
    assert result.exit_code == 0
    assert "Resolved Dependencies" in result.output
    assert "WhateverGreen" in result.output


def test_cli_deps_resolve_json_output(t480s_baseline_fixture: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out_file = tmp_path / "lock.json"
    result = runner.invoke(
        cli,
        ["deps", "resolve", "-f", str(t480s_baseline_fixture), "-m", "sequoia", "--json", "-o", str(out_file)],
    )
    assert result.exit_code == 0
    assert out_file.is_file()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["target_model"] == "Lenovo ThinkPad T480s"
    assert len(data["resolved_dependencies"]) > 0


def test_cli_deps_cache_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["deps", "cache"])
    assert result.exit_code == 0
    assert "Local Dependency Cache" in result.output


def test_cli_deps_cache_clear() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["deps", "cache", "--clear"])
    assert result.exit_code == 0
    assert "cleared" in result.output.lower()
