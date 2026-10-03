"""Guided clients expose semantic choices without engineering inputs."""
import asyncio
import json
from pathlib import Path

from click.testing import CliRunner
from textual.widgets import Input, Static

from macloader.autoloader.models import ActionKind, NextAction, Stage
from macloader.autoloader.service import AutoloaderService
from macloader.identity.service import IdentityService, IdentityServiceError
from macloader.ui.cli import cli
from macloader.ui.guided import GuidedApp
from tests.unit.test_autoloader import candidate
import pytest


def test_one_profile_acceptance_binds_all_current_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    action = NextAction(Stage.ACCEPTANCE, ActionKind.HUMAN, "Accept prototype", choices=("Accept prototype",))
    monkeypatch.setattr(service, "next_action", lambda: action)
    service.perform_choice("Accept prototype")
    assert service.configuration is not None and service.snapshot is not None
    evaluation = service.workflow.evaluate(service.configuration, service.snapshot).evaluation
    assert not any(issue.code == "ACKNOWLEDGEMENT_REQUIRED" for issue in evaluation.issues)
    altered = service.workflow.set_option(service.configuration, "profile.audio", "layout-11")
    assert any(issue.code == "ACKNOWLEDGEMENT_REQUIRED" for issue in service.workflow.evaluate(altered, service.snapshot).evaluation.issues)


def test_identity_reuse_does_not_request_filename_and_synthetic_generation_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    IdentityService(service.identity_root).store(IdentityService.fake_identity())
    action = NextAction(Stage.IDENTITY, ActionKind.HUMAN, "Choose private identity", choices=("Generate new", "Reuse existing"))
    monkeypatch.setattr(service, "next_action", lambda: action)
    with pytest.raises(IdentityServiceError, match="Synthetic"):
        service.perform_choice("Generate new")
    service.perform_choice("Reuse existing")
    assert service.configuration is not None and service.configuration.identity_ref is not None
    assert "P4TEST" not in json.dumps(service.public_status())
    with pytest.raises(ValueError, match="not valid"):
        service.perform_choice("path/to/identity.json")


def test_guided_screen_has_no_engineering_inputs(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = AutoloaderService(root=tmp_path)
        service.start(candidate(), private_material="test")
        for size in ((80, 24), (100, 35)):
            app = GuidedApp(service)
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert not list(app.query(Input))
                content = str(app.query_one("#campaign-review", Static).render())
                assert "24A335" in content and "experimental" in content
                assert service.configuration is not None
                assert service.configuration.configuration_id not in content
                await pilot.click("#retry-guided")
                await app.workers.wait_for_complete()
    asyncio.run(exercise())


def test_guided_cli_status_hides_identifiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material="test")
    monkeypatch.setattr("macloader.autoloader.service.AutoloaderService", lambda: service)
    monkeypatch.setattr(service, "start", service.next_action)
    result = CliRunner().invoke(cli, ["autoload", "--status"])
    assert result.exit_code == 0, result.output
    assert "24A335" in result.output
    assert "Firmware-table collection" in result.output
    assert service.configuration is not None and service.configuration.configuration_id not in result.output


def test_default_entrypoint_opens_guided_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = AutoloaderService(root=tmp_path)
    opened: list[bool] = []
    monkeypatch.setattr("macloader.autoloader.service.AutoloaderService", lambda: service)
    monkeypatch.setattr(GuidedApp, "run", lambda _self: opened.append(True))
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0, result.output
    assert opened == [True]
