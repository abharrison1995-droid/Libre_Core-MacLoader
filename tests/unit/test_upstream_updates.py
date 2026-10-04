"""Maintenance candidates stay isolated and cannot bypass required gates."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest
import yaml
from macloader.database.loader import Database
from macloader.dependencies.downloader import Downloader
from macloader.maintenance.upstream import UpstreamUpdater, candidate_for, overlay, repository_slug


def inputs(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    notice = b'BSD fixture notice; not a real upstream license'
    (tmp_path / 'notice').write_bytes(notice)
    spec = dict(id='lilu', version='1.0.0', release_tag='1.0.0', upstream_repository='https://github.com/acidanthera/Lilu',
        license='BSD-3-Clause', license_file='notice', license_sha256=hashlib.sha256(notice).hexdigest(),
        artifacts={'RELEASE': dict(asset_name='Lilu-1.0.0-RELEASE.zip', archive_type='zip')})
    payload = b'candidate bytes'
    asset = dict(name='Lilu-2.0.0-RELEASE.zip', browser_download_url='https://github.com/acidanthera/Lilu/releases/download/2.0.0/Lilu-2.0.0-RELEASE.zip',
        size=len(payload), digest='sha256:' + hashlib.sha256(payload).hexdigest())
    release = dict(tag_name='2.0.0', draft=False, prerelease=False, html_url='https://github.com/acidanthera/Lilu/releases/tag/2.0.0', assets=[asset])
    license_record = dict(encoding='base64', content=base64.b64encode(notice).decode(), license={'spdx_id': 'BSD-3-Clause'})
    return spec, release, license_record


def test_valid_candidate_pinned_download_and_wrong_hash(tmp_path: Path) -> None:
    spec, release, notice = inputs(tmp_path)
    candidate = candidate_for(spec, release, notice, tmp_path)
    artifact = candidate.assets['RELEASE']
    assert candidate.tag == '2.0.0' and artifact.sha256 == hashlib.sha256(b'candidate bytes').hexdigest()
    good = Downloader(transport=lambda url, path: None if path.write_bytes(b'candidate bytes') else None)
    good.download_artifact(artifact, tmp_path / 'candidate.zip')
    bad = Downloader(transport=lambda url, path: None if path.write_bytes(b'incorrect bytes') else None)
    with pytest.raises(Exception):
        bad.download_artifact(artifact, tmp_path / 'bad.zip')
    assert not (tmp_path / 'bad.zip').exists()


@pytest.mark.parametrize('change', ['draft', 'origin', 'hash', 'ambiguous', 'license', 'missing-notice'])
def test_malformed_metadata_and_license_require_review(tmp_path: Path, change: str) -> None:
    spec, release, notice = inputs(tmp_path)
    if change == 'draft':
        release['draft'] = True
    elif change == 'origin':
        release['assets'][0]['browser_download_url'] = 'https://evil.example/payload'
    elif change == 'hash':
        release['assets'][0]['digest'] = None
    elif change == 'ambiguous':
        release['assets'].append(deepcopy(release['assets'][0]))
    elif change == 'license':
        notice['content'] = base64.b64encode(b'changed license').decode()
    else:
        (tmp_path / 'notice').unlink()
    with pytest.raises(ValueError):
        candidate_for(spec, release, notice, tmp_path)


def test_unchanged_monitor_no_proposal_and_tool_migration_report(tmp_path: Path) -> None:
    db = Database()
    pins = {repository_slug(spec.upstream_repository): spec.release_tag for spec in db.list_dependency_specs()}
    pins['open-acpica/acpica'] = '20260408'
    requests: list[str] = []
    def metadata(endpoint: str) -> dict[str, Any]:
        requests.append(endpoint)
        slug = endpoint.removeprefix('repos/').removesuffix('/releases/latest')
        return dict(tag_name=pins[slug])
    original = (db.data_dir / 'dependencies/catalog.yaml').read_bytes()
    updater = UpstreamUpdater(db, metadata)
    report = updater.watch(tmp_path)
    assert not report['candidate_directory'] and report['proposal_ready'] is False and report['auto_merge'] is False
    assert len(requests) == len(set(requests))
    assert original == (db.data_dir / 'dependencies/catalog.yaml').read_bytes()
    pins['acidanthera/OpenCorePkg'] = '1.0.8'
    report = updater.watch(tmp_path)
    assert any(r['state'] == 'migration-required' for r in report['reports'])
    assert not report['candidate_directory']


def test_verified_candidate_overlay_validates_existing_graph_and_tamper_blocks(tmp_path: Path) -> None:
    db = Database()
    specs = {spec.id: spec for spec in db.list_dependency_specs()}
    pins = {repository_slug(spec.upstream_repository): spec.release_tag for spec in specs.values()}
    pins['open-acpica/acpica'] = '20260408'
    lilu = specs['lilu']
    notice = (db.data_dir / lilu.license_file).read_bytes()
    def metadata(endpoint: str) -> dict[str, Any]:
        if '/license?' in endpoint:
            return dict(encoding='base64', content=base64.b64encode(notice).decode(), license={'spdx_id': lilu.license})
        slug = endpoint.removeprefix('repos/').removesuffix('/releases/latest')
        if slug != 'acidanthera/Lilu':
            return dict(tag_name=pins[slug])
        assets = []
        for artifact in lilu.artifacts.values():
            name = artifact.asset_name.replace(lilu.version, '9.9.9')
            assets.append(dict(name=name, browser_download_url=f'https://github.com/{slug}/releases/download/9.9.9/{name}', size=4, digest='sha256:' + hashlib.sha256(b'test').hexdigest()))
        return dict(tag_name='9.9.9', draft=False, prerelease=False, html_url=f'https://github.com/{slug}/releases/tag/9.9.9', assets=assets)
    report = UpstreamUpdater(db, metadata, Downloader(transport=lambda url, p: None if p.write_bytes(b'test') else None)).watch(tmp_path / 'output')
    candidate = Path(report['candidate_directory'])
    assert report['proposal_ready'] is False and report['required_gates']
    proposed = Database(overlay(candidate, tmp_path / 'overlay'))
    assert proposed.get_dependency_spec('lilu').version == '9.9.9'  # type: ignore[union-attr]
    assert db.get_dependency_spec('lilu').version == lilu.version  # type: ignore[union-attr]
    catalog = yaml.safe_load((candidate / 'catalog.yaml').read_text())
    catalog['dependencies'][1]['dependencies'] = ['absent_dependency']
    (candidate / 'catalog.yaml').write_text(yaml.safe_dump(catalog))
    with pytest.raises(ValueError, match='provenance'):
        overlay(candidate, tmp_path / 'tampered')
    with pytest.raises(ValueError):
        repository_slug('http://github.com/acidanthera/Lilu')


def test_workflow_blocks_proposal_on_any_gate_failure_and_has_no_auto_merge() -> None:
    workflow = Path(__file__).parents[2] / '.github/workflows/upstream-candidates.yml'
    raw = yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)
    jobs = raw['jobs']
    assert jobs['propose']['needs'] == ['prepare', 'candidate-matrix']
    assert "needs.candidate-matrix.result == 'success'" in jobs['propose']['if']
    matrix = jobs['candidate-matrix']['strategy']['matrix']
    assert matrix['os'] == ['ubuntu-latest', 'windows-latest']
    assert matrix['python'] == ['3.11', '3.14']
    steps = '\n'.join(step.get('run', '') for step in jobs['candidate-matrix']['steps'])
    for required in ('--cov-fail-under=0', '--fail-under=79', 'mypy', 'MACLOADER_VERIFY_CANDIDATE_ASSETS', 'ocvalidate', 'package_smoke'):
        # Asset verification is an explicit test-step environment, not shell interpolation.
        assert required in steps or required in json.dumps(jobs['candidate-matrix'])
    publish = next(step for step in jobs['propose']['steps'] if step.get('name', '').startswith('Create draft PR'))
    assert 'publish_proposal' in publish['if'] and '--draft' in publish['run'] and '--body-file' in publish['run']
    assert 'gh pr merge' not in workflow.read_text() and '--force' not in workflow.read_text()
