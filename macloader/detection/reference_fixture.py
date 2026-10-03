"""Sanitize a supplied live capture for regression; never physical evidence."""
from macloader.detection.sanitize import sanitize_hardware_snapshot, _sanitize_data_structure
from macloader.domain.hardware import HardwareSnapshot


def reference_fixture(snapshot: HardwareSnapshot) -> HardwareSnapshot:
    from macloader.database.loader import get_database
    db = get_database()
    if snapshot.raw_evidence.get('synthetic_fixture') or snapshot.raw_evidence.get('fixture_is_not_evidence'):
        raise ValueError('A real reference fixture requires a supplied live capture')
    if db.candidate_campaign(snapshot) is None:
        raise ValueError('Capture does not match the frozen machine and BIOS')
    safe = sanitize_hardware_snapshot(snapshot)
    data = _sanitize_data_structure(safe.to_dict())
    data.update(snapshot_id='reference-t480s-20l8-n22et85w-162-regression-only', timestamp='2000-01-01T00:00:00Z', serial_number=None, uuid=None)
    data['raw_evidence'] = dict(os='sanitized-live-reference-fixture', fixture_is_not_evidence=True,
        inventory_status=snapshot.get_inventory_status(), provenance='Sanitized supplied capture; regression input, never physical acceptance')
    for section in ('wifi', 'ethernet', 'bluetooth'):
        for device in data[section]:
            device['mac_address'] = None
    for disk in data['storage']:
        disk['serial'] = None
    return HardwareSnapshot.from_dict(data)
