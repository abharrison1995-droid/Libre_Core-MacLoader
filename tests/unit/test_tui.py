"""Headless Textual smoke tests for the shared workflow presentation."""

import asyncio
import json
from pathlib import Path
from textual.widgets import Button

from macloader.configuration.store import ConfigurationStore
from macloader.ui.tui import WorkflowApp
from macloader.workflow.service import WorkflowService
from macloader.evidence.usb import UsbEvidenceSession, UsbPortObservation
from macloader.domain.recovery import RecoveryState, RecoveryTarget
from macloader.recovery.discovery import RecoveryDiscoveryResult


def test_tui_mounts_and_runs_shared_actions(t480s_baseline_fixture: Path, tmp_path: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))

    async def exercise() -> None:
        app = WorkflowApp(fixture=t480s_baseline_fixture, service=service)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#workflow-status").render() is not None
            assert app.query_one("#workflow-stages").render() is not None
            app._apply_target()
            assert app._draft is not None
            app.action_save()
            assert "Saved configuration" in str(app.query_one("#workflow-status").render())
            app.action_cancel()
            assert "Cancelled" in str(app.query_one("#workflow-status").render())

    asyncio.run(exercise())


def test_tui_resume_requires_explicit_fixture(tmp_path: Path, t480s_baseline_fixture: Path) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, _ = service.create(t480s_baseline_fixture)
    service.save(draft)

    async def exercise() -> None:
        app = WorkflowApp(config_id=draft.configuration_id, service=service)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "--fixture is required" in str(app.query_one("#workflow-status").render())
        resumed = WorkflowApp(config_id=draft.configuration_id, fixture=t480s_baseline_fixture, service=service)
        async with resumed.run_test() as pilot:
            await pilot.pause()
            assert resumed._snapshot is not None

    asyncio.run(exercise())


def test_tui_exposes_non_destructive_workflow_stages(
    tmp_path: Path, t480s_baseline_fixture: Path
) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    export_path = tmp_path / "public.json"
    evidence_path = tmp_path / "usb.json"
    evidence_path.write_text(
        json.dumps(
            UsbEvidenceSession(
                "fixture-t480s-baseline", "N22ET85W", "private/usb.json", "collector-1",
                (UsbPortObservation("left-a", "HS01", "USB-A", "5Gbps", "8086:9d2f"),),
            ).to_dict()
        ),
        encoding="utf-8",
    )

    async def exercise() -> None:
        app = WorkflowApp(fixture=t480s_baseline_fixture, service=service)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.on_button_pressed(Button.Pressed(app.query_one("#cancel", Button)))
            app.on_button_pressed(Button.Pressed(Button("unmapped")))
            app._apply_target()
            app.query_one("#config-path-input").value = str(export_path)
            app._export_config()
            assert export_path.is_file()
            app._import_config()
            assert app._draft is not None
            app._migrate_config()
            _, app._snapshot = service.create(t480s_baseline_fixture)
            app.query_one("#evidence-path-input").value = str(evidence_path)
            app.query_one("#evidence-kind-input").value = "usb"
            app._import_evidence()
            app._support_review()
            app._resolve_dependencies()
            await pilot.pause(1.0)
            app.query_one("#efi-output-input").value = str(tmp_path / "efi-output")
            app._build_efi()
            await pilot.pause(1.0)
            app.query_one("#media-device-id-input").value = "USB-EXAMPLE"
            app.query_one("#media-model-input").value = "Disposable USB"
            app.query_one("#media-capacity-input").value = "16000000000"
            app.query_one("#media-required-input").value = "1000000000"
            app._media_plan()
            app._usb_status()
            app._verify_recovery()
            app.query_one("#version-input").value = "99.0"
            app.query_one("#build-input").value = "99Z99"
            app._apply_target()
            app.query_one("#ack-rule-input").value = "unknown-rule"
            app.query_one("#ack-warning-input").value = "not a policy warning"
            app._acknowledge()
            app._recovery_result = RecoveryDiscoveryResult(
                RecoveryState.UNAVAILABLE,
                RecoveryTarget("sequoia", "macOS Sequoia", "15.0", "24A335"),
                None,
                "a" * 64,
                ("exact product unavailable",),
            )
            app._acquire_recovery()
            assert "blocked" in str(app.query_one("#workflow-status").render()).lower()
            app._draft = None
            app.action_save()
            assert "Nothing to save" in str(app.query_one("#workflow-status").render())
            app._apply_target()
            app._set_option()
            app._acknowledge()
            app._import_evidence()
            app._resolve_dependencies()
            app._build_efi()
            app._evaluate()
            await pilot.pause(0.5)

    asyncio.run(exercise())
