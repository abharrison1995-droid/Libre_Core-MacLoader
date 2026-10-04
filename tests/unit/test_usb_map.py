"""Evidence-backed map generation uses synthetic firmware/physical declarations."""
from dataclasses import replace
import plistlib
import pytest
from macloader.build.usb_map import generate_usb_map
from macloader.build.config import SchemaDrivenConfigGenerator
from macloader.database.loader import get_database
from macloader.evidence.usb import UsbEvidenceSession, UsbPortObservation
from macloader.exceptions import BuildPlanError


def evidence() -> UsbEvidenceSession:
    observations = []
    for route, label, speed, internal, address in [
        ('SS01', 'left USB-A beside HDMI', 5000, False, 17),
        ('SS02', 'right USB-A beside cooling vent', 5000, False, 18),
        ('HS01', 'left USB-A beside HDMI', 480, False, 1),
        ('HS02', 'right USB-A beside cooling vent', 480, False, 2),
        ('SS03', 'Integrated card reader', 5000, True, 19),
        ('HS07', 'Intel Bluetooth', 12, True, 7),
        ('HS08', 'Integrated camera', 480, True, 8),
        ('HS06', 'Unsupported WWAN', 480, True, 6)]:
        observations.append(UsbPortObservation(label, route, 'internal' if internal else 'USB-A', f'{speed}Mbps', '0000:00:14.0', internal_device=internal, port_address=address, namespace_path=f'_SB.PCI0.XHC.{route}'))
    return UsbEvidenceSession('synthetic', 'N22ET85W-1.62', 'private/usb.json', 'guided-usb-1', tuple(observations), unresolved_logical_correlation=('charging USB-C',))


def generate(source: UsbEvidenceSession, **kwargs: object):  # type: ignore[no-untyped-def]
    campaign = next(iter(get_database().campaigns.values()))
    values: dict[str, object] = dict(snapshot_id='synthetic', bios_binding=campaign.bios_binding, smbios='MacBookPro15,2', first_route=campaign.profile.usb.first_install_route,
        addresses={o.namespace_path: o.port_address for o in source.observations}, controller_slots={'0000:00:14.0'})
    values.update(kwargs)
    return generate_usb_map(source, campaign.evidence_policy['usb_capture'], **values)  # type: ignore[arg-type]


def test_map_is_deterministic_codeless_and_bound_to_source() -> None:
    source = evidence()
    generated = generate(source)
    assert generated.digest == generate(source).digest
    assert generated.evidence_digest == source.to_evidence_record().digest
    assert generated.first_install_label == 'left USB-A beside HDMI'
    info = plistlib.loads(generated.plist)
    personality = info['IOKitPersonalities']['MacLoader-XHC']
    ports = personality['IOProviderMergeProperties']['ports']
    assert set(ports) == {'SS01', 'SS02', 'HS01', 'HS02', 'SS03', 'HS07', 'HS08'}
    assert ports['HS07']['UsbConnector'] == 255 and ports['SS01']['port'] == b'\x11\x00\x00\x00'
    assert personality['model'] == 'MacBookPro15,2'
    assert SchemaDrivenConfigGenerator._kernel_entry('MacLoaderUSBMap.kext')['ExecutablePath'] == ''


@pytest.mark.parametrize('case', ['snapshot', 'bios', 'minimum', 'companion', 'controller', 'address', 'namespace', 'duplicate', 'internal', 'conflict'])
def test_map_fails_closed_when_proof_is_missing(case: str) -> None:
    source = evidence()
    kwargs: dict[str, object] = {}
    if case in {'snapshot', 'bios'}:
        kwargs['snapshot_id' if case == 'snapshot' else 'bios_binding'] = 'wrong'
    elif case in {'minimum', 'companion'}:
        source = replace(source, observations=tuple(o for o in source.observations if o.logical_port != ('HS08' if case == 'minimum' else 'HS01')))
    elif case == 'controller':
        kwargs['controller_slots'] = {'different'}
    elif case == 'address':
        kwargs['addresses'] = {}
    elif case == 'namespace':
        source = replace(source, observations=(replace(source.observations[0], namespace_path='_SB.PCI0.OTHER.SS01'), *source.observations[1:]))
    elif case == 'duplicate':
        source = replace(source, observations=(replace(source.observations[0], port_address=18), *source.observations[1:]))
    elif case == 'internal':
        source = replace(source, observations=tuple(replace(o, internal_device=False) if o.logical_port == 'HS07' else o for o in source.observations))
    else:
        source = replace(source, observations=(*source.observations, source.observations[0]))
    with pytest.raises((BuildPlanError, ValueError)):
        generate(source, **kwargs)
