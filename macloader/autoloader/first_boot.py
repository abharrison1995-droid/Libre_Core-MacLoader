"""Human-reported first-boot checkpoints never constitute hardware acceptance."""
from macloader.autoloader.models import ActionKind, NextAction, Stage
from macloader.database.campaigns import ReferenceCampaign
from macloader.domain.contracts import canonical_json_digest
from macloader.evidence.usb import UsbEvidenceSession

CHECKPOINTS = (
    ('picker', 'OpenCore picker was reached.'),
    ('recovery-entry', 'Recovery entry was selected.'),
    ('recovery-utilities', 'Recovery utilities screen was reached.'),
    ('no-erase', 'No disk was erased.'),
    ('no-install', 'No macOS installation was started.'),
    ('shutdown', 'The laptop was shut down or rebooted.'),
    ('existing-os', 'The existing operating system still boots.'),
)


def instructions(campaign: ReferenceCampaign, usb: UsbEvidenceSession) -> str:
    if campaign.first_boot_instruction != 'lenovo-f12-recovery-smoke':
        raise ValueError('First-boot procedure is not reviewed')
    route = campaign.profile.usb.first_install_route
    labels = {o.physical_label for o in usb.observations if o.logical_port == route and o.connector_type == 'USB-A' and not o.internal_device}
    if len(labels) != 1:
        raise ValueError('First-install physical USB route is unproven')
    label = next(iter(labels))
    return (f'Smoke test only: Recovery build is unproven; the profile remains experimental.\n'
        f'1. Shut down. Connect the prepared USB to {label}.\n'
        '2. Power on and press F12. Select the UEFI entry for that USB.\n'
        '3. At the OpenCore picker, select Recovery. Stop at the utilities screen.\n'
        '4. Do not erase a disk or start installation. Shut down or reboot.\n'
        '5. Remove the USB and verify that the existing OS still boots.\n'
        'Record each result below. Installation needs a separate future target approval.')


def checkpoint_binding(configuration: str, campaign: str, media: str) -> str:
    return canonical_json_digest(dict(configuration=configuration, campaign=campaign, media=media, purpose='reported-first-boot-only'))


def next_checkpoint(results: dict[str, str], procedure: str) -> NextAction:
    for name, statement in CHECKPOINTS:
        result = results.get(name)
        if result == 'failed':
            return NextAction(Stage.FIRST_BOOT, ActionKind.HUMAN,
                f'Checkpoint not confirmed: {statement} No successful boot or installation authority was recorded. Preserve diagnostics and leave disks untouched. Retry only after reviewing the failure.',
                'FIRST_BOOT_FAILED', ('Repeat this checkpoint',))
        if result != 'confirmed':
            return NextAction(Stage.FIRST_BOOT, ActionKind.HUMAN, procedure + '\n\nConfirm the performed result: ' + statement,
                'FIRST_BOOT_CHECKPOINT', ('Confirmed', 'Failed or not reached'))
    return NextAction(Stage.COMPLETE, ActionKind.COMPLETE,
        'Picker/Recovery smoke results are recorded as human reports. The profile remains experimental; exact-build qualification and hardware acceptance are unproven. Internal-disk installation is not authorized.', 'FIRST_BOOT_REPORTED')


def record_checkpoint(results: dict[str, str], choice: str) -> None:
    for name, _ in CHECKPOINTS:
        if results.get(name) == 'failed' and choice == 'Repeat this checkpoint':
            results.pop(name)
            return
        if results.get(name) != 'confirmed':
            if choice not in {'Confirmed', 'Failed or not reached'}:
                raise ValueError('Invalid first-boot result')
            results[name] = 'confirmed' if choice == 'Confirmed' else 'failed'
            return
    raise ValueError('First-boot results are already complete')
