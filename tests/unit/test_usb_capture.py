"""Physical event simulations are never hardware acceptance evidence."""
from dataclasses import replace
from pathlib import Path
import pytest

from macloader.evidence.acpi_capture import CaptureError
from macloader.evidence.usb import UsbEvidenceSession
from macloader.evidence.usb_capture import UsbCaptureStep, UsbDeviceEvent, UsbEvidenceCollector, LinuxUsbEventProvider, firmware_usb_addresses


class Events:
    devices: tuple[UsbDeviceEvent, ...] = ()
    def enumerate(self) -> tuple[UsbDeviceEvent, ...]:
        return self.devices


def event(logical: str = 'SS01', token: str = 'synthetic-device', speed: int = 5000) -> UsbDeviceEvent:
    return UsbDeviceEvent('1-1', token, '0000:00:14.0', f'_SB.PCI0.XHC.{logical}', logical, 17 if logical.startswith('SS') else 1, speed)


def wizard(provider: Events, saved: list[dict[str, object]]) -> UsbEvidenceCollector:
    evidence = UsbEvidenceSession('synthetic-snapshot', 'BIOS', 'private/usb.json', 'guided-usb-1')
    return UsbEvidenceCollector(evidence, (UsbCaptureStep('left USB-A', 'USB-A', 'super'), UsbCaptureStep('right USB-A', 'USB-A', 'super')), provider, saved.append)


def test_insertion_removal_resume_and_test_identity() -> None:
    provider = Events()
    saved: list[dict[str, object]] = []
    collector = wizard(provider, saved)
    assert 'Remove' in collector.instruction
    assert not collector.poll()
    assert 'Insert' in collector.instruction
    provider.devices = (event(),)
    assert not collector.poll() and collector.cursor == 1
    assert not collector.poll()  # removal required
    provider.devices = ()
    assert not collector.poll()
    provider.devices = (event('SS02', 'wrong'),)
    with pytest.raises(CaptureError, match='same test device'):
        collector.poll()
    resumed = UsbEvidenceCollector(collector.session, collector.steps, provider, saved.append, saved[-1])
    assert resumed.cursor == 1 and 'Remove' in resumed.instruction
    provider.devices = ()
    resumed.poll()
    provider.devices = (replace(event('SS02'), port_address=18),)
    assert resumed.poll()
    assert resumed.session.completeness[0].value == 'complete'
    assert resumed.poll()


@pytest.mark.parametrize('case', ['multiple', 'speed', 'no-route', 'duplicate'])
def test_invalid_event_does_not_accept_observation(case: str) -> None:
    provider = Events()
    saved: list[dict[str, object]] = []
    collector = wizard(provider, saved)
    collector.poll()
    if case == 'multiple':
        provider.devices = (event(), replace(event('SS02'), event_id='1-2'))
    elif case == 'speed':
        provider.devices = (event(speed=480),)
    elif case == 'no-route':
        provider.devices = (replace(event(), port_address=None),)
    else:
        collector.session = replace(collector.session, observations=())
        provider.devices = (event(),)
        collector.poll()
        collector.cursor = 0
        provider.devices = ()
        collector.poll()
        provider.devices = (event(),)
    with pytest.raises(CaptureError):
        collector.poll()


def test_usb_c_both_orientations_and_unresolved_are_preserved() -> None:
    provider = Events()
    saved: list[dict[str, object]] = []
    evidence = UsbEvidenceSession('snapshot', 'BIOS', 'private/usb.json', '1')
    steps = (UsbCaptureStep('charging USB-C', 'USB-C', 'super', 'normal'), UsbCaptureStep('charging USB-C', 'USB-C', 'super', 'flipped'))
    collector = UsbEvidenceCollector(evidence, steps, provider, saved.append)
    collector.poll()
    provider.devices = (replace(event('SS04'), port_address=20),)
    collector.poll()
    provider.devices = ()
    collector.poll()
    provider.devices = (replace(event('SS04'), port_address=20),)
    assert collector.poll()
    assert collector.session.completeness[0].value == 'complete'
    unresolved = UsbEvidenceCollector(evidence, steps[:1], provider, saved.append)
    provider.devices = ()
    unresolved.poll()
    provider.devices = (replace(event(), logical_port='', port_address=None),)
    assert unresolved.poll()
    assert unresolved.session.unresolved_logical_correlation == ('charging USB-C',)


def test_static_firmware_proof_cannot_execute_or_guess() -> None:
    dsl = '''Scope (\\_SB) { Device (PCI0) { Device (XHC) {
        Device (HS01) { Name (_ADR, One) }
        Device (SS01) { Name (_ADR, 0x11) }
        Device (HS02) { Method (TEST) { Name (_ADR, 2) } }
        Device (HS03) { Name (_ADR, COMPUTED ()) }
    } } }'''
    assert firmware_usb_addresses(dsl) == {'_SB.PCI0.XHC.HS01': 1, '_SB.PCI0.XHC.SS01': 17}
    with pytest.raises(CaptureError, match='ambiguous'):
        firmware_usb_addresses(dsl.replace('Name (_ADR, One)', 'Name (_ADR, One) Name (_ADR, 3)'))
    assert not firmware_usb_addresses('Device (FAKE) { Name (_ADR, 2) }')


def test_linux_provider_uses_firmware_path_and_private_token(tmp_path: Path) -> None:
    usb = tmp_path / '0000:00:14.0' / 'usb1'
    device = usb / '1-1'
    port = usb / '1-0:1.0' / 'usb1-port1'
    (port / 'firmware_node').mkdir(parents=True)
    device.mkdir()
    for name, text in {'idVendor': '1234', 'idProduct': '5678', 'serial': 'UNIQUE-PRIVATE-SERIAL', 'speed': '5000'}.items():
        (device / name).write_text(text)
    (port / 'firmware_node' / 'path').write_text('\\_SB_.PCI0.XHC_.SS01')
    (port / 'connect_type').write_text('hardwired')
    root = tmp_path / 'devices'
    root.mkdir()
    try:
        (root / '1-1').symlink_to(device, target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit synthetic sysfs links')
    provider = LinuxUsbEventProvider('private-key', {'_SB.PCI0.XHC.SS01': 17}, root)
    detected = provider.enumerate()
    assert len(detected) == 1 and detected[0].internal and detected[0].port_address == 17
    assert 'UNIQUE-PRIVATE-SERIAL' not in repr(detected)
    (device / 'speed').write_text('unknown')
    assert provider.enumerate() == ()
    with pytest.raises(CaptureError):
        LinuxUsbEventProvider('key', {}, tmp_path / 'missing').enumerate()


def test_progress_rejects_out_of_range_or_invalid_tokens() -> None:
    provider = Events()
    saved: list[dict[str, object]] = []
    collector = wizard(provider, saved)
    invalid: tuple[dict[str, object], ...] = ({'cursor': 99}, {'tokens': ['wrong']})
    for progress in invalid:
        with pytest.raises(ValueError):
            UsbEvidenceCollector(collector.session, collector.steps, provider, saved.append, progress)


def test_private_decompiler_output_yields_only_literal_addresses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from macloader.evidence.usb_capture import collect_firmware_usb_addresses
    from macloader.build.acpi import AcpiProcessor
    from tests.unit.test_acpi_capture import table
    source = tmp_path / 'capture' / 'PRIVATE-ACPI'
    source.mkdir(parents=True)
    data = source / 'dsdt.dat'
    data.write_bytes(table(b'DSDT'))
    monkeypatch.setattr(AcpiProcessor, '_verify_tool', lambda _: None)
    monkeypatch.setattr(AcpiProcessor, '_find_tables', lambda _: (data,))
    def run(_self: AcpiProcessor, args: list[str], diagnostic: Path, **kwargs: object) -> tuple[int, str, str]:
        prefix = Path(args[2])
        prefix.with_suffix('.dsl').write_text('Scope (\\_SB) { Device (PCI0) { Device (XHC) { Device (SS01) { Name (_ADR, 0x11) } } } }')
        return 0, '', ''
    monkeypatch.setattr(AcpiProcessor, '_run', run)
    result = collect_firmware_usb_addresses(source.parent, tmp_path / 'synthetic-iasl', '0' * 64, tmp_path / 'private', lambda: False)
    assert result == {'_SB.PCI0.XHC.SS01': 17}
    assert not list((tmp_path / 'private').iterdir())


def test_guided_wizard_owns_paths_and_attaches_only_completed_physical_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    from types import SimpleNamespace
    from macloader.autoloader.service import AutoloaderService
    from macloader.autoloader.models import Stage
    from macloader.evidence.acpi_capture import LinuxAcpiCaptureProvider
    from tests.unit.test_acpi_capture import sysfs
    from tests.unit.test_autoloader import candidate
    import macloader.evidence.usb_capture as module
    service = AutoloaderService(root=tmp_path / 'private')
    service.start(candidate(), private_material='test')
    assert service.configuration is not None and service.snapshot is not None
    source = tmp_path / 'source'
    source.mkdir()
    sysfs(source)
    snapshot = service.snapshot
    service.save_configuration(service.workflow.collect_acpi(service.configuration, snapshot, LinuxAcpiCaptureProvider(source), lambda: snapshot))
    assert service.next_action().stage == Stage.USB
    with pytest.raises(CaptureError, match='Synthetic'):
        service._prepare_usb()
    snapshot.raw_evidence.pop('synthetic_fixture')
    internal = tuple(replace(event(route, speed=speed), event_id=f'internal-{route}', internal=True, port_address=address) for route, speed, address in [('SS03', 5000, 19), ('HS07', 12, 7), ('HS08', 480, 8)])
    provider = Events()
    provider.devices = internal
    monkeypatch.setattr(module, 'LinuxUsbEventProvider', lambda *args: provider)
    monkeypatch.setattr(module, 'collect_firmware_usb_addresses', lambda *args: {})
    monkeypatch.setattr('macloader.toolchain.loader.TrustedToolchainLoader.provision', lambda _: SimpleNamespace(acpi_compiler_path='synthetic', acpi_compiler_sha256='0' * 64))
    monkeypatch.setattr(service, '_current_bound_snapshot', lambda: snapshot)
    # Simulate the Linux provider on either CI host; actual Windows correlation remains blocked.
    import macloader.autoloader.service as service_module
    monkeypatch.setattr(service_module.sys, 'platform', 'linux')
    service.perform_choice('Begin port checks')
    assert service.usb_collector is not None
    collector = service.usb_collector
    for step in collector.steps:
        provider.devices = internal
        service.advance_until_blocked()  # arm insertion after removal
        if step.connector == 'USB-A':
            route = ('SS01' if step.label.startswith('left') else 'SS02') if step.speed == 'super' else ('HS01' if step.label.startswith('left') else 'HS02')
            detected = event(route, token=step.speed, speed=5000 if step.speed == 'super' else 480)
            detected = replace(detected, port_address={'SS01': 17, 'SS02': 18, 'HS01': 1, 'HS02': 2}[route])
        else:
            detected = replace(event(speed=5000 if step.speed == 'super' else 480), device_token=step.speed, logical_port='', port_address=None)
        provider.devices = (*internal, detected)
        service.advance_until_blocked()
    record = next(r for r in service.configuration.evidence if r.kind == 'usb')
    assert record.completeness.value == 'partial' and record.physical_port_evidence and record.input_scope
    assert len(json.loads(Path(record.private_ref).read_text())['observations']) == 7
    assert service.next_action().stage == Stage.TOOLS
