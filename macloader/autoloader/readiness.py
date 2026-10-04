"""Read-only readiness evidence; unknown gates always remain blocked."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from macloader.autoloader.service import AutoloaderService


@dataclass(frozen=True)
class ReadinessGate:
    name: str
    passed: bool
    detail: str


EXPECTED_JOBS = {'test (ubuntu-latest, 3.11)', 'test (ubuntu-latest, 3.14)',
    'test (windows-latest, 3.11)', 'test (windows-latest, 3.14)',
    'wheel-smoke (ubuntu-latest)', 'wheel-smoke (windows-latest)'}


def hosted_ci_gate(root: Path | None = None) -> ReadinessGate:
    """Read GitHub's actual jobs for this exact checkout commit, never a flag."""
    cwd = root or Path.cwd()
    def run(args: list[str]) -> str:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True, timeout=30).stdout
    try:
        commit = run(['git', 'rev-parse', 'HEAD']).strip()
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('Invalid commit')
        changes = run(['git', 'status', '--porcelain']).splitlines()
        if any(not line[3:].startswith('docs/') for line in changes):
            return ReadinessGate('Known green software commit', False, 'Uncommitted software changes need their own hosted checks.')
        runs = json.loads(run(['gh', 'run', 'list', '--workflow', 'ci.yml', '--commit', commit, '--limit', '10', '--json', 'databaseId,headSha,status,conclusion']))
        for item in runs:
            if item.get('headSha') != commit or item.get('status') != 'completed' or item.get('conclusion') != 'success':
                continue
            jobs = json.loads(run(['gh', 'run', 'view', str(item['databaseId']), '--json', 'jobs']))['jobs']
            successful = {job['name'] for job in jobs if job.get('conclusion') == 'success'}
            if EXPECTED_JOBS <= successful:
                return ReadinessGate('Known green software commit', True, f'All required hosted jobs passed for {commit[:12]}.')
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    return ReadinessGate('Known green software commit', False, 'Exact-commit hosted functional, type, coverage and package evidence is unavailable or incomplete.')


def campaign_readiness(service: 'AutoloaderService', ci: ReadinessGate | None = None) -> dict[str, Any]:
    gates = [ci or hosted_ci_gate()]
    session, configuration, snapshot = service.session, service.configuration, service.snapshot
    live = snapshot is not None and not snapshot.raw_evidence.get('synthetic_fixture') and not snapshot.raw_evidence.get('fixture_is_not_evidence')
    gates.append(ReadinessGate('Live reference machine', bool(live and service.match and service.match.ready), 'Current machine, BIOS and component facts must match; a fixture never proves physical evidence.'))
    evaluation_ok = False
    if configuration is not None and snapshot is not None:
        try:
            evaluation_ok = not service.workflow.evaluate(configuration, snapshot).evaluation.has_blockers
        except Exception:
            pass
    gates.append(ReadinessGate('Current evidence and deliberate decisions', evaluation_ok and bool(live), 'Current ACPI, USB, policy acceptance and private identity must validate.'))
    efi_ok = False
    if session and 'efi' in session.artifacts:
        try:
            service._current_efi_manifest()
            efi_ok = True
        except Exception:
            pass
    gates.append(ReadinessGate('Current validated EFI', efi_ok, 'Actual bytes, map, manifest and matching pinned tools must revalidate.'))
    recovery_ok = False
    if session and configuration and service.match and service.match.campaign and 'recovery' in session.artifacts:
        try:
            from macloader.recovery.smoke import SmokeRecoveryService, VerifiedSmokeRecovery
            artifact = session.artifacts['recovery']
            path = Path(artifact['path'])
            record = VerifiedSmokeRecovery.from_dict(service.workflow._read_private_json(path / 'smoke.json', 'smoke Recovery'))
            SmokeRecoveryService(service.match.campaign.smoke_recovery_policy).verify(record, path, service._recovery_binding())
            recovery_ok = (artifact.get('mode') == 'smoke' and record.digest == artifact['digest']
                and session.artifacts.get('recovery_choice', {}).get('mode') == 'smoke')
        except Exception:
            pass
    gates.append(ReadinessGate('Explicit trusted Recovery mode', recovery_ok, 'Smoke mode proves signed bytes only; exact 24A335 qualification remains unproven.'))
    gates.append(ReadinessGate('Physically qualified Windows writer', service.media.qualified, 'ADR-007 Windows-first qualification and safe eject are mandatory; the default adapter is disabled.'))
    written = bool(session and session.artifacts.get('media_write', {}).get('readback') == 'complete'
        and session.artifacts.get('media_write', {}).get('eject') == 'complete'
        and any(a.get('state') == 'readback-eject-complete' for a in session.actions))
    gates.append(ReadinessGate('Confirmed USB write, full readback and eject', written, 'A performed write needs exact-target consent, complete readback and eject; disposable tests cannot supply this.'))
    route = False
    if configuration:
        try:
            service._first_boot_instructions()
            route = True
        except Exception:
            pass
    gates.append(ReadinessGate('Proven physical first-install route', route and bool(live), 'F12 instructions must use current physical port evidence.'))
    ready = all(g.passed for g in gates)
    return dict(ready_for_first_boot=ready, installation_authorized=False, support_state='EXPERIMENTAL',
        gates=[dict(name=g.name, passed=g.passed, detail=g.detail) for g in gates])
