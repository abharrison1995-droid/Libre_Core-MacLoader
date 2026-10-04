"""Automatic software preparation and private map/build binding simulations."""
from dataclasses import replace
import os
import hashlib
import json
from pathlib import Path
from typing import Any
import zipfile
import pytest

from macloader.autoloader.service import AutoloaderService
from macloader.autoloader.models import ActionKind, Stage
from macloader.evidence.acpi_capture import LinuxAcpiCaptureProvider
from macloader.identity.service import IdentityService
from tests.unit.test_autoloader import candidate
from tests.unit.test_acpi_capture import sysfs
from tests.unit.test_usb_map import evidence


def ready(tmp_path: Path) -> AutoloaderService:
    service = AutoloaderService(root=tmp_path / 'private')
    service.start(candidate(), private_material='test')
    assert service.configuration is not None and service.snapshot is not None
    source = tmp_path / 'source'
    source.mkdir()
    sysfs(source)
    snapshot = service.snapshot
    service.save_configuration(service.workflow.collect_acpi(service.configuration, snapshot, LinuxAcpiCaptureProvider(source), lambda: snapshot))
    usb_path = service.root / 'usb.json'
    usb = replace(evidence(), snapshot_id=snapshot.snapshot_id, private_ref=str(usb_path))
    service.workflow._write_private_json(usb_path, json.dumps(usb.to_dict()))
    service.save_configuration(service.workflow.add_evidence(service.configuration, usb.to_evidence_record()))
    private = IdentityService(service.identity_root).store(IdentityService.fake_identity())
    service.save_configuration(service.workflow.set_identity_reference(service.configuration, private.storage_ref))
    draft = service.configuration
    policy = service.workflow.orchestrator.configuration_service.policy
    for option in policy.options.values():
        if option.requires_acknowledgement:
            draft = service.workflow.orchestrator.configuration_service.acknowledge(draft, option.option_id, option.explanation)
    service.save_configuration(draft)
    evaluation = service.workflow.evaluate(service.configuration, snapshot).evaluation
    assert not evaluation.has_blockers, [(i.code, i.explanation) for i in evaluation.issues if i.blocking]
    return service


def test_software_actions_prepare_tools_dependencies_then_stop_before_real_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_p4_toolchain import _synthetic_loader
    service = ready(tmp_path)
    selection = _synthetic_loader(tmp_path).select()
    monkeypatch.setattr('macloader.toolchain.loader.TrustedToolchainLoader.provision', lambda _: selection)
    acquired: list[object] = []
    monkeypatch.setattr(service.workflow.orchestrator, 'fetch_dependencies', lambda *args, **kwargs: acquired.append(args[0]))
    action = service.advance_until_blocked()
    assert action.code == 'SYNTHETIC_BUILD_DISABLED'
    assert service.session is not None
    assert {'tools', 'dependencies'} <= set(service.session.artifacts)
    assert 'efi' not in service.session.artifacts and len(acquired) == 1
    assert all(a['state'] in {'complete', 'failed'} for a in service.session.actions)


def test_profiled_builder_consumes_generated_map_and_binds_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_p4_toolchain import _synthetic_loader
    from macloader.build.acpi import AcpiBuildResult, AcpiProcessor
    from macloader.build.efi import EfiBuilder
    from macloader.database.loader import Database
    from macloader.domain.dependencies import ArtifactVariant
    from macloader.dependencies.resolver import DependencyResolver
    from macloader.configuration.service import ConfigurationService
    from macloader.toolchain.loader import TrustedToolchainLoader
    from macloader.build.config import load_reviewed_profile
    service = ready(tmp_path)
    assert service.configuration is not None and service.snapshot is not None
    data_root = os.environ.get("MACLOADER_CANDIDATE_DATA_DIR")
    db = Database(data_root) if data_root else Database()
    preliminary = ConfigurationService(db).evaluate(service.configuration, service.snapshot).plan
    resolver = DependencyResolver(db)
    initial = resolver.resolve(preliminary)
    archives = {}
    for dep in initial.resolved_dependencies:
        spec = db.get_dependency_spec(dep.dependency_id)
        assert spec is not None
        artifact = spec.get_artifact(ArtifactVariant.RELEASE)
        assert artifact is not None
        archive = tmp_path / f'{dep.dependency_id}.zip'
        if os.environ.get("MACLOADER_VERIFY_CANDIDATE_ASSETS") == "1":
            from macloader.dependencies.downloader import Downloader
            Downloader(timeout=120).download_artifact(artifact, archive)
            archives[dep.dependency_id] = archive
            continue
        with zipfile.ZipFile(archive, 'w') as z:
            for component in spec.subcomponents:
                name = f'Release/{component}'
                if component.endswith('.kext'):
                    name += '/Contents/Info.plist'
                z.writestr(name, b'synthetic component')
        artifact.sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        artifact.size_bytes = archive.stat().st_size
        archives[dep.dependency_id] = archive
    plan = ConfigurationService(db).evaluate(service.configuration, service.snapshot).plan
    dependencies = DependencyResolver(db).resolve(plan)
    tools = TrustedToolchainLoader().select() if os.environ.get("MACLOADER_VERIFY_PINNED_TOOLS") == "1" else _synthetic_loader(tmp_path).select()
    monkeypatch.setattr(TrustedToolchainLoader, 'verify_selection', lambda _self, selected: selected)
    record = next(r for r in service.configuration.evidence if r.kind == 'acpi')
    capture = Path(record.private_ref).parent
    acpi_digest = AcpiProcessor.capture_evidence_digest(capture, record.bios_binding, service.snapshot.snapshot_id)
    def acpi_build(_self: AcpiProcessor, source: Path, output: Path, **kwargs: Any) -> AcpiBuildResult:
        return AcpiBuildResult((), acpi_digest, 'b' * 64, tools.digest, (), 'synthetic-only')
    monkeypatch.setattr(AcpiProcessor, 'build', acpi_build)
    usb_record = next(r for r in service.configuration.evidence if r.kind == 'usb')
    usb = evidence()
    def addresses(*args: Any) -> dict[str, int]:
        # Stand-in for current firmware disassembly; no real capture is used.
        args[3].mkdir(parents=True)
        return {o.namespace_path: o.port_address for o in usb.observations if o.port_address is not None}
    monkeypatch.setattr('macloader.evidence.usb_capture.collect_firmware_usb_addresses', addresses)
    builder = EfiBuilder(db=db, identity_store_dir=service.identity_root)
    result = builder.build(plan, dependencies, archives, tmp_path / 'output', toolchain=tools,
        reviewed_profile=load_reviewed_profile(), private_acpi_capture=capture,
        expected_acpi_evidence_digest=acpi_digest, private_usb_evidence=Path(usb_record.private_ref),
        hardware_snapshot=service.snapshot, identity_reference=service.configuration.identity_ref, synthetic_test_mode=True)
    assert result.validation.status == 'VALID'
    assert len(result.manifest.evidence_digests) == 3
    assert usb_record.digest in result.manifest.evidence_digests
    import plistlib
    config = plistlib.loads((result.output_dir / 'EFI/OC/config.plist').read_bytes())
    assert any(entry['BundlePath'] == 'MacLoaderUSBMap.kext' and entry['ExecutablePath'] == '' for entry in config['Kernel']['Add'])
    info = result.output_dir / 'EFI/OC/Kexts/MacLoaderUSBMap.kext/Contents/Info.plist'
    assert hashlib.sha256(info.read_bytes()).hexdigest() in result.manifest.evidence_digests
    assert b'P4TEST' not in (result.output_dir / 'manifest.json').read_bytes()


def test_mutated_raw_firmware_is_rejected_before_acceptance(tmp_path: Path) -> None:
    from tests.unit.test_acpi_capture import table
    service = ready(tmp_path)
    assert service.configuration is not None and service.snapshot is not None
    record = next(r for r in service.configuration.evidence if r.kind == 'acpi')
    path = Path(record.private_ref).parent / 'PRIVATE-ACPI/ssdt.dat'
    path.write_bytes(table(b'SSDT', 99))  # even a new structurally valid table is stale evidence
    evaluation = service.workflow.evaluate(service.configuration, service.snapshot).evaluation
    assert any(i.code == 'EVIDENCE_SOURCE_INVALID' for i in evaluation.issues)
    assert evaluation.accepted is None
