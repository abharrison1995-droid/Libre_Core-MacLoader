"""Reviewed packaged approval only; local reports never enable production I/O."""
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
from typing import Any

import yaml

REQUIRED_CASES = (
    'normal-write', 'exact-target-revalidation', 'disappeared', 'identity-changed',
    'unexpected-mount', 'read-only', 'short-write', 'cancelled', 'flush-failed',
    'readback-corruption', 'eject-failed', 'reconnect-success', 'reconnect-invalidated',
    'unrelated-usb', 'system-disks-present',
)


def backend_digest() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ('windows_native.py', 'windows_image.py', 'windows_qualification.py', 'adapters.py', 'writer.py'):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def runtime_platform() -> tuple[str, str, str]:
    return sys.platform, platform.version(), platform.machine().upper()


def valid_approval(record: Any, digest: str, host: tuple[str, str, str]) -> bool:
    from macloader.removable.windows_native import BACKEND_VERSION
    if not isinstance(record, dict) or host[0] != 'win32' or host[2] != 'AMD64':
        return False
    return (record.get('backend_version') == BACKEND_VERSION and record.get('backend_sha256') == digest
        and record.get('windows_version') == host[1] and record.get('architecture') == host[2]
        and record.get('reviewed') is True
        and isinstance(record.get('physical_report_sha256'), str)
        and re.fullmatch(r'[0-9a-f]{64}', record['physical_report_sha256']) is not None
        and isinstance(record.get('review_url'), str)
        and re.fullmatch(r'https://github.com/abharrison1995-droid/Libre_Core-MacLoader/pull/[1-9][0-9]*', record['review_url']) is not None
        and isinstance(record.get('cases'), dict)
        and set(record['cases']) == set(REQUIRED_CASES)
        and all(value == 'physical-pass' for value in record['cases'].values()))


def approved_backend() -> bool:
    try:
        path = Path(__file__).parents[1] / 'database/data/media/windows-writer-approvals.yaml'
        payload = yaml.safe_load(path.read_text())
        if not isinstance(payload, dict) or payload.get('schema') != 1 or not isinstance(payload.get('approvals'), list):
            return False
        return any(valid_approval(row, backend_digest(), runtime_platform()) for row in payload['approvals'])
    except (OSError, ValueError, yaml.YAMLError):
        return False


def qualification_report(public_device_ref: str, commit: str, results: dict[str, str]) -> dict[str, object]:
    """Human reports are stored as unreviewed physical evidence, never approval."""
    if not re.fullmatch(r'device-[0-9a-f]{16}', public_device_ref) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid redacted qualification binding')
    if set(results) - set(REQUIRED_CASES) or any(value not in ('physical-pass', 'physical-fail', 'not-run') for value in results.values()):
        raise ValueError('Invalid physical case report')
    return {'schema': 1, 'backend_sha256': backend_digest(), 'backend_version': 'windows-native-gpt-fat32-v1',
        'platform': list(runtime_platform()), 'commit': commit, 'device': public_device_ref,
        'cases': {name: results.get(name, 'not-run') for name in REQUIRED_CASES},
        'reviewed': False, 'production_approved': False, 'bootable': False}



class FaultInjectingStorage:
    """Qualification-only, labelled injection around real selected native I/O."""
    def __init__(self, api: Any, scenario: str):
        self.api, self.scenario, self.triggered = api, scenario, False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.api, name)

    def write(self, handle: int, offset: int, data: bytes) -> None:
        if self.scenario == 'short-write' and not self.triggered and len(data) > 512:
            self.triggered = True
            self.api.write(handle, offset, data[:512])
            raise OSError('Controlled partial-write injection')
        self.api.write(handle, offset, data)
        if self.scenario == 'cancelled':
            self.triggered = True

    def flush(self, handle: int) -> None:
        if self.scenario == 'flush-failed' and not self.triggered:
            self.triggered = True
            raise OSError('Controlled flush-failure injection')
        self.api.flush(handle)

    def read(self, handle: int, offset: int, size: int) -> bytes:
        data = bytes(self.api.read(handle, offset, size))
        if self.scenario == 'readback-corruption' and not self.triggered:
            self.triggered = True
            return bytes([data[0] ^ 1]) + data[1:]
        return bytes(data)

    def ioctl(self, handle: int, code: int, data: bytes = b'', size: int = 65536) -> bytes:
        from macloader.removable.windows_native import SET_ATTRIBUTES
        if self.scenario == 'eject-failed' and code == SET_ATTRIBUTES and not self.triggered:
            self.triggered = True
            raise OSError('Controlled offline-failure injection')
        return bytes(self.api.ioctl(handle, code, data, size))

def run_physical_qualification(report_path: Path, scenario: str = "normal") -> None:
    """Separate explicit engineering harness; never sets an approval capability."""
    import tempfile
    import subprocess
    import click
    from macloader.removable.adapters import WindowsRemovableAdapter
    from macloader.removable.windows_native import WindowsNativeBackend
    from macloader.removable.writer import DestructiveConfirmation, MediaBindings, RemovableMediaWriter, UnsafeRemovableTarget
    from macloader.workflow.service import WorkflowService
    if sys.platform != 'win32':
        raise UnsafeRemovableTarget('Physical qualification requires an administrator Windows host')
    # Read-only exact commit CI first. Never a user-supplied "green" flag.
    from macloader.autoloader.readiness import hosted_ci_gate
    gate = hosted_ci_gate()
    if not gate.passed:
        raise UnsafeRemovableTarget('Physical qualification needs a clean exact known-green commit')
    if report_path.exists():
        raise UnsafeRemovableTarget('Use a new private qualification report; existing evidence is preserved')
    adapter = WindowsRemovableAdapter()
    devices = []
    for device in adapter.enumerate():
        try:
            RemovableMediaWriter._assert_safe(device, 512 * 1024 * 1024)
            if device.serial and 1024 ** 3 <= device.capacity_bytes <= 128 * 1024 ** 3:
                devices.append(device)
        except UnsafeRemovableTarget:
            pass
    if not devices:
        raise UnsafeRemovableTarget('No unique unmounted sacrificial USB is eligible; no write occurred')
    click.echo('NON-BOOTABLE writer qualification. Only the selected sacrificial USB is erased.')
    for index, device in enumerate(devices, 1):
        click.echo(f'{index}. {device.model} · {device.capacity_bytes / 1024 ** 3:.1f} GiB · {device.public_device_ref}')
    index = click.prompt('Select the exact sacrificial USB', type=click.IntRange(1, len(devices)))
    selected = devices[index - 1]
    click.confirm(f'Erase {selected.model} · {selected.capacity_bytes / 1024 ** 3:.1f} GiB · {selected.public_device_ref} for NON-BOOTABLE qualification?', abort=True)
    def inventory() -> list[dict[str, Any]]:
        payload = json.loads(WindowsRemovableAdapter._run_powershell(WindowsRemovableAdapter._powershell_query()))
        rows = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(row, dict) for row in rows):
            raise UnsafeRemovableTarget('Windows qualification inventory is invalid')
        return rows
    if scenario not in ('normal', 'short-write', 'cancelled', 'flush-failed', 'readback-corruption', 'eject-failed'):
        raise UnsafeRemovableTarget('Unsupported qualification scenario')
    fault: FaultInjectingStorage | None = None
    if scenario == 'normal':
        backend = WindowsNativeBackend(inventory)
    else:
        from macloader.removable.windows_native import Win32Storage
        fault = FaultInjectingStorage(Win32Storage(), scenario)
        backend = WindowsNativeBackend(inventory, api=fault)
        if scenario == 'cancelled':
            backend.set_cancel(lambda: bool(fault and fault.triggered))
    def write(plan: Any, source: Path) -> None:
        backend.lock_and_dismount(plan.target)
        backend.write(plan, source)
        backend.flush(plan.target)
    writer = RemovableMediaWriter(destructive_write=write, enumerator=adapter.enumerate,
        readback_verifier=backend.readback, invalidator=lambda plan, reason: backend.invalidate(plan.target, reason),
        require_published_artifacts=False)  # Qualification payload, never campaign media.
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report = qualification_report(selected.public_device_ref, commit, {})
    WorkflowService._ensure_private_directory(report_path.parent)
    def save() -> None:
        WorkflowService._write_private_json(report_path, json.dumps(report, indent=2))
    report.update(operation='not-started', scenario=scenario, method='native-normal' if scenario == 'normal' else 'controlled-fault-injection-on-physical-medium')
    save()  # Journal private report before any destructive boundary.
    try:
        with tempfile.TemporaryDirectory(prefix='macloader-writer-qualification-') as temporary:
            source = Path(temporary)
            WorkflowService._ensure_private_directory(source)
            for name in ('EFI/BOOT/BOOTx64.efi', 'EFI/OC/OpenCore.efi', 'Recovery/test.bin'):
                path = source / name
                WorkflowService._ensure_private_directory(path.parent)
                path.write_bytes(b'MACLOADER NON-BOOTABLE QUALIFICATION ONLY\n' * 2048)
                WorkflowService._protect_private_file(path)
            stamp = hashlib.sha256(b'non-bootable-writer-qualification').hexdigest()
            bindings = MediaBindings(*(stamp for _ in range(6)))
            plan = writer.dry_run(selected, 512 * 1024 * 1024, source, bindings)
            report.update(operation='may-have-written', plan_digest=plan.plan_digest)
            save()
            writer.write(plan, source, DestructiveConfirmation.issue(plan))
            backend.safe_eject(selected)
            if fault is not None:
                raise UnsafeRemovableTarget('Expected qualification fault did not occur')
            report['operation'] = 'full-readback-offline-complete'
            cases = report['cases']
            assert isinstance(cases, dict)
            cases['normal-write'] = 'physical-pass'
            cases['exact-target-revalidation'] = 'physical-pass'
    except BaseException:
        try:
            backend.invalidate(selected, 'Qualification failed or interrupted')
        finally:
            report['operation'] = 'failed-usb-unready'
            save()
        if fault is not None and fault.triggered and backend.invalidation_complete:
            cases = report['cases']
            assert isinstance(cases, dict)
            cases[scenario] = 'physical-pass'
            report['operation'] = 'injected-failure-withheld-ready-invalidation-offline-complete'
            save()
            click.echo('Controlled injected failure withheld readiness; signature readback/offline completed. This tests failure handling on a physical medium, not an actual driver fault. Production remains unapproved.')
            return
        raise UnsafeRemovableTarget('Writer qualification failed; selected USB may be erased and is unready. Private report preserved.') from None
    save()
    click.echo('Write, full readback and offline completed. USB is NON-BOOTABLE; qualification remains unreviewed. Reconnect and perform the remaining documented cases.')



def record_physical_case(report_path: Path, case: str, result: str) -> None:
    """Explicit operator report, with immutable backend/platform/commit binding."""
    import subprocess
    from macloader.workflow.service import WorkflowService
    from macloader.autoloader.readiness import hosted_ci_gate
    if not hosted_ci_gate().passed:
        raise ValueError('A physical report needs the exact known-green checkout')
    report = WorkflowService._read_private_json(report_path, 'writer qualification')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if (report.get('schema') != 1 or report.get('backend_sha256') != backend_digest()
        or report.get('platform') != list(runtime_platform()) or report.get('commit') != commit
        or report.get('production_approved') is not False or report.get('reviewed') is not False
        or report.get('bootable') is not False or not isinstance(report.get('cases'), dict)
        or set(report['cases']) != set(REQUIRED_CASES)):
        raise ValueError('Physical report binding changed; preserve it and start a new campaign')
    if case not in REQUIRED_CASES or result not in ('physical-pass', 'physical-fail'):
        raise ValueError('Invalid physical result')
    report['cases'][case] = result
    WorkflowService._write_private_json(report_path, json.dumps(report, indent=2))


import click


@click.command()
@click.option('--run', 'run_device', is_flag=True, help='Explicit sacrificial USB normal-case qualification; destructive confirmation follows.')
@click.option('--report', type=click.Path(path_type=Path), help='Private qualification report (Engineering harness only).')
@click.option('--scenario', type=click.Choice(('normal', 'short-write', 'cancelled', 'flush-failed', 'readback-corruption', 'eject-failed')), default='normal', help='Qualification-only controlled fault on a real sacrificial medium.')
@click.option('--record-case', type=click.Choice(REQUIRED_CASES), help='Record a physically performed fault/reconnect scenario; never approve production.')
@click.option('--result', type=click.Choice(('physical-pass', 'physical-fail')))
def cli(run_device: bool, report: Path | None, record_case: str | None, result: str | None, scenario: str) -> None:
    if run_device and record_case:
        raise click.UsageError('Run and result recording are separate checkpoints')
    if not run_device and record_case is None:
        if result:
            raise click.UsageError('--result requires --record-case')
        click.echo(json.dumps({'backend_sha256': backend_digest(), 'platform': runtime_platform(),
            'production_approved': approved_backend(), 'physical_cases_required': REQUIRED_CASES}, indent=2))
        return
    if report is None:
        raise click.UsageError('Qualification needs a private --report path')
    if record_case and result is None:
        raise click.UsageError('--record-case requires --result')
    try:
        if record_case:
            click.confirm('Record this result only if you physically performed the documented sacrificial USB scenario?', abort=True)
            assert result is not None
            record_physical_case(report, record_case, result)
            click.echo('Human physical report saved. Production remains unapproved.')
        else:
            run_physical_qualification(report, scenario)
    except Exception:
        raise click.ClickException('Qualification did not complete. No medium is approved; review the private report and readiness gates.') from None


if __name__ == '__main__':
    cli()
