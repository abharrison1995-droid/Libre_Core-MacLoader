"""Cancelled or superseded TUI operations must not publish or persist results."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path
import threading
from typing import Any, Callable, Optional

import pytest
from textual.widgets import Input

import macloader.workflow.service as workflow_module
from macloader.configuration.store import ConfigurationStore
from macloader.domain.configuration import UserConfiguration
from macloader.domain.evidence import EvidenceCompleteness, EvidenceRecord
from macloader.domain.hardware import HardwareSnapshot
from macloader.identity.service import IdentityServiceError
from macloader.ui.tui import WorkflowApp
from macloader.workflow.service import PRIVATE_IDENTITY_CONFIRMATION, WorkflowService


class GatedService(WorkflowService):
    """Blocks inside the worker until the test releases it."""

    def __init__(self, store: ConfigurationStore) -> None:
        super().__init__(store=store)
        self.release = threading.Event()
        self.entered = threading.Event()
        self.saved: list[UserConfiguration] = []
        self.discarded: list[str] = []
        self.cancel_seen: list[bool] = []

    def save(self, configuration: UserConfiguration) -> Path:
        self.saved.append(configuration)
        return super().save(configuration)

    def _wait(self, cancel: Optional[Callable[[], bool]]) -> None:
        self.entered.set()
        assert self.release.wait(10)
        self.cancel_seen.append(bool(cancel and cancel()))

    def import_acpi_capture(
        self, configuration: UserConfiguration, snapshot: HardwareSnapshot, source_directory: Path,
        cancel: Optional[Callable[[], bool]] = None,
        *,
        allow_synthetic_snapshot: bool = False,
    ) -> tuple[UserConfiguration, EvidenceRecord]:
        self._wait(cancel)
        record = EvidenceRecord(
            evidence_id="acpi-synthetic", kind="acpi", schema_version="1", digest="c" * 64,
            private_ref="/nonexistent/evidence.json", machine_snapshot_id=snapshot.snapshot_id,
            bios_binding="N22ET85W-1.62", capture_method="synthetic", capture_version="1",
            completeness=EvidenceCompleteness.COMPLETE,
        )
        return replace(configuration, revision=configuration.revision + 1), record

    def discard_acpi_capture(self, record: EvidenceRecord) -> None:  # type: ignore[override]
        self.discarded.append(record.evidence_id)

    def generate_private_identity(
        self, configuration: UserConfiguration, confirmation: str,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> tuple[UserConfiguration, str]:
        self._wait(cancel)
        return replace(configuration, revision=configuration.revision + 1), "a" * 32 + ".json"

    def discard_private_identity(self, storage_ref: str) -> None:  # type: ignore[override]
        self.discarded.append(storage_ref)


def _saved_draft(service: WorkflowService, fixture: Path) -> tuple[UserConfiguration, HardwareSnapshot]:
    draft, snapshot = service.create(fixture)
    draft = service.set_target(draft, "15.0", "24A335")
    service.save(replace(draft, revision=0))
    return service.load(draft.configuration_id), snapshot


def _run(
    tmp_path: Path, fixture: Path, start: Callable[[WorkflowApp], None],
    interrupt: Optional[Callable[[WorkflowApp], None]],
) -> tuple[GatedService, WorkflowApp, UserConfiguration]:
    service = GatedService(ConfigurationStore(tmp_path / "configs"))
    draft, snapshot = _saved_draft(service, fixture)
    service.saved.clear()
    app = WorkflowApp(service=service)

    async def exercise() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app._draft, app._snapshot, app.config_id = draft, snapshot, draft.configuration_id
            start(app)
            await asyncio.get_running_loop().run_in_executor(None, service.entered.wait, 10)
            if interrupt is not None:
                interrupt(app)
            service.release.set()
            for _ in range(50):
                await pilot.pause(0.05)
                if service.cancel_seen and (service.saved or service.discarded):
                    break

    asyncio.run(exercise())
    return service, app, draft


def _start_acpi(app: WorkflowApp) -> None:
    app.query_one("#acpi-capture-input", Input).value = "/synthetic/capture"
    app._import_acpi_capture()


def _start_identity(app: WorkflowApp) -> None:
    app.query_one("#identity-checkpoint-input", Input).value = PRIVATE_IDENTITY_CONFIRMATION
    app._generate_identity()


@pytest.mark.parametrize("start", [_start_acpi, _start_identity], ids=["acpi", "identity"])
@pytest.mark.parametrize(
    "interrupt",
    [
        pytest.param(lambda app: app.action_cancel(), id="cancelled"),
        pytest.param(lambda app: app._run_preflight(), id="superseded"),
    ],
)
def test_stale_operation_is_discarded_not_persisted(
    tmp_path: Path, t480s_baseline_fixture: Path,
    start: Callable[[WorkflowApp], None], interrupt: Callable[[WorkflowApp], None],
) -> None:
    service, app, draft = _run(tmp_path, t480s_baseline_fixture, start, interrupt)
    assert service.cancel_seen == [True]
    assert service.saved == []
    assert len(service.discarded) == 1
    assert app._draft is not None and app._draft.revision == draft.revision
    assert service.load(draft.configuration_id).revision == draft.revision


@pytest.mark.parametrize("start", [_start_acpi, _start_identity], ids=["acpi", "identity"])
def test_current_operation_is_committed_on_the_ui_thread(
    tmp_path: Path, t480s_baseline_fixture: Path, start: Callable[[WorkflowApp], None]
) -> None:
    service, app, draft = _run(tmp_path, t480s_baseline_fixture, start, None)
    assert service.cancel_seen == [False]
    assert len(service.saved) == 1 and service.discarded == []
    assert app._draft is not None and app._draft.revision == draft.revision + 1


def test_recovery_discovery_generation_advances_on_the_ui_thread(
    tmp_path: Path, t480s_baseline_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    started: list[int] = []
    monkeypatch.setattr(WorkflowApp, "_discover_recovery_worker", lambda self, generation: started.append(generation))

    async def exercise() -> None:
        app = WorkflowApp(fixture=t480s_baseline_fixture, service=service)
        async with app.run_test() as pilot:
            await pilot.pause()
            before = app._operation_generation
            app._discover_recovery()
            assert app._operation_generation == before + 1 and started == [before + 1]

    asyncio.run(exercise())


# --- service-level commit points ---------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="uses the Linux sysfs capture route")
def test_cancelled_acpi_import_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    from test_acpi_capture import _sysfs
    from macloader.evidence.acpi_capture import capture_acpi_tables

    tables, dmi = _sysfs(tmp_path / "sys")
    capture = tmp_path / "capture"
    capture_acpi_tables(capture, tables_root=tables, dmi_root=dmi)
    acpi_root = tmp_path / "private" / "acpi"
    monkeypatch.setattr(workflow_module, "DEFAULT_PRIVATE_DIR", tmp_path / "private")
    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", acpi_root)
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(fixtures_dir / "t480s" / "t480s_20l8_bios162_synthetic.json")
    with pytest.raises(ValueError, match="cancelled before publication"):
        service.import_acpi_capture(configuration, snapshot, capture, cancel=lambda: True, allow_synthetic_snapshot=True)
    assert list(acpi_root.iterdir()) == []

    _updated, record = service.import_acpi_capture(configuration, snapshot, capture, allow_synthetic_snapshot=True)
    imported = Path(record.private_ref).parent
    assert imported.is_dir()
    service.discard_acpi_capture(record)
    assert not imported.exists()
    with pytest.raises(ValueError, match="can be discarded"):
        service.discard_acpi_capture(replace(record, private_ref=str(tmp_path / "evidence.json")))


def test_cancelled_identity_generation_stops_before_tools_and_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, t480s_baseline_fixture: Path
) -> None:
    from macloader.toolchain.loader import TrustedToolchainLoader

    provisioned: list[bool] = []
    monkeypatch.setattr(TrustedToolchainLoader, "provision", lambda _self, **_kwargs: provisioned.append(True))
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, _snapshot = service.create(t480s_baseline_fixture)
    draft = service.set_target(draft, "15.0", "24A335")
    with pytest.raises(IdentityServiceError, match="nothing was generated"):
        service.generate_private_identity(draft, PRIVATE_IDENTITY_CONFIRMATION, cancel=lambda: True)
    assert provisioned == []


def test_cancel_between_generation_and_storage_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, t480s_baseline_fixture: Path
) -> None:
    from macloader.identity.service import IdentityService
    from macloader.toolchain.loader import TrustedToolchainLoader

    identity_dir = tmp_path / "identities"
    monkeypatch.setattr(workflow_module, "DEFAULT_IDENTITY_DIR", identity_dir)
    tool = type("Selection", (), {"identity_tool_path": str(tmp_path / "macserial")})()
    monkeypatch.setattr(TrustedToolchainLoader, "provision", lambda _self, **_kwargs: tool)
    monkeypatch.setattr(IdentityService, "generate", lambda _self, **_kwargs: IdentityService.fake_identity())
    stored: list[Any] = []
    monkeypatch.setattr(IdentityService, "store", lambda _self, values, **_kwargs: stored.append(values))
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    draft, _snapshot = service.create(t480s_baseline_fixture)
    draft = service.set_target(draft, "15.0", "24A335")
    answers = iter([False, True])
    with pytest.raises(IdentityServiceError, match="nothing was stored"):
        service.generate_private_identity(draft, PRIVATE_IDENTITY_CONFIRMATION, cancel=lambda: next(answers))
    assert stored == []


def test_discard_private_identity_only_removes_generated_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity_dir = tmp_path / "identities"
    identity_dir.mkdir()
    monkeypatch.setattr(workflow_module, "DEFAULT_IDENTITY_DIR", identity_dir)
    generated = identity_dir / ("b" * 32 + ".json")
    generated.write_text("{}", encoding="utf-8")
    WorkflowService.discard_private_identity(generated.name)
    assert not generated.exists()
    for bad in ("../x.json", "reviewed-identity.json", "b" * 32 + ".txt"):
        with pytest.raises(ValueError, match="can be discarded"):
            WorkflowService.discard_private_identity(bad)
