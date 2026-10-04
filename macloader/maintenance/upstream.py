"""Authoritative release monitoring and isolated, reviewable catalog candidates."""
import argparse
import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable
from urllib.parse import quote, urlparse
import yaml

from macloader.database.loader import Database
from macloader.dependencies.downloader import Downloader
from macloader.domain.contracts import canonical_json_digest
from macloader.domain.dependencies import DependencyArtifact


MAX_METADATA = 2 * 1024 * 1024


def github_metadata(endpoint: str) -> dict[str, Any]:
    if not re.fullmatch(r'repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(releases/latest|license\?ref=[A-Za-z0-9_.%+-]+)', endpoint):
        raise ValueError('Only catalog-bound release/license endpoints are permitted')
    result = subprocess.run(['gh', 'api', endpoint], capture_output=True, text=True, check=True, timeout=30)
    if len(result.stdout.encode()) > MAX_METADATA:
        raise ValueError('Release metadata exceeds the bounded limit')
    raw = json.loads(result.stdout)
    if not isinstance(raw, dict):
        raise ValueError('Release metadata must be an object')
    return raw


def repository_slug(url: str) -> str:
    parsed = urlparse(url)
    slug = parsed.path.strip('/')
    if parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', slug):
        raise ValueError('Upstream repository must be an authoritative GitHub catalog URL')
    return slug


@dataclass(frozen=True)
class ReleaseCandidate:
    dependency_id: str
    repository: str
    tag: str
    assets: dict[str, DependencyArtifact]
    license_digest: str


def candidate_for(spec: dict[str, Any], release: dict[str, Any], license_record: dict[str, Any], data_root: Path) -> ReleaseCandidate:
    slug = repository_slug(spec['upstream_repository'])
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not re.fullmatch(r'v?\d+(?:\.\d+){0,3}', tag) or release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('Only stable, unambiguous version tags are eligible')
    if release.get('html_url') != f'https://github.com/{slug}/releases/tag/{tag}':
        raise ValueError('Release identity differs from the catalog repository')
    notice = data_root / spec['license_file']
    if not notice.is_file() or notice.is_symlink() or hashlib.sha256(notice.read_bytes()).hexdigest() != spec['license_sha256']:
        raise ValueError('Existing license notice is missing or changed')
    if license_record.get('encoding') != 'base64' or license_record.get('license', {}).get('spdx_id') != spec['license']:
        raise ValueError('Candidate license cannot be established')
    encoded = license_record.get('content')
    if not isinstance(encoded, str) or len(encoded) > MAX_METADATA:
        raise ValueError('Candidate license notice is missing or oversized')
    source_notice = base64.b64decode(encoded, validate=False)
    license_digest = hashlib.sha256(source_notice).hexdigest()
    if not source_notice or license_digest != spec['license_sha256']:
        raise ValueError('Candidate license notice changed; explicit license review is required')
    assets = release.get('assets')
    if not isinstance(assets, list) or len(assets) > 128:
        raise ValueError('Candidate asset metadata is malformed')
    proposed: dict[str, DependencyArtifact] = {}
    for variant, previous in spec['artifacts'].items():
        name = previous['asset_name'].replace(spec['version'], tag.removeprefix('v'))
        matches = [a for a in assets if isinstance(a, dict) and a.get('name') == name]
        if len(matches) != 1:
            raise ValueError('Exact candidate release asset is missing or ambiguous')
        asset = matches[0]
        digest = asset.get('digest', '')
        size = asset.get('size')
        url = asset.get('browser_download_url')
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest) or type(size) is not int or not 0 < size <= 512 * 1024 * 1024:
            raise ValueError('Candidate needs an authoritative SHA-256 and bounded byte count')
        if url != f'https://github.com/{slug}/releases/download/{tag}/{name}':
            raise ValueError('Candidate asset origin or release identity changed')
        proposed[variant] = DependencyArtifact.from_dict(dict(asset_name=name, source_url=url,
            sha256=digest.removeprefix('sha256:'), size_bytes=size, variant=variant, archive_type=previous['archive_type']))
    return ReleaseCandidate(spec['id'], slug, tag, proposed, license_digest)


class UpstreamUpdater:
    def __init__(self, db: Database | None = None, metadata: Callable[[str], dict[str, Any]] = github_metadata,
                 downloader: Downloader | None = None):
        self.db = db or Database()
        self.metadata = metadata
        self.downloader = downloader or Downloader(timeout=120)

    def watch(self, output: Path) -> dict[str, Any]:
        catalog = yaml.safe_load((self.db.data_dir / 'dependencies/catalog.yaml').read_text())
        reports: list[dict[str, Any]] = []
        releases: dict[str, dict[str, Any]] = {}
        candidates: list[ReleaseCandidate] = []
        for spec in catalog['dependencies']:
            slug = repository_slug(spec['upstream_repository'])
            report: dict[str, Any] = dict(dependency=spec['id'], repository=slug, pinned=spec['release_tag'])
            try:
                if slug not in releases:
                    releases[slug] = self.metadata(f'repos/{slug}/releases/latest')
                release = releases[slug]
                report['latest'] = release.get('tag_name')
                if release.get('tag_name') == spec['release_tag']:
                    report['state'] = 'unchanged'
                elif spec['id'] == 'opencore':
                    report.update(state='migration-required', detail='OpenCore EFI, schema and matching tools need a separate versioned migration; frozen campaign stays unchanged.')
                else:
                    tag = release.get('tag_name')
                    if not isinstance(tag, str) or not re.fullmatch(r'v?\d+(?:\.\d+){0,3}', tag):
                        raise ValueError('Unreviewed tag format')
                    license_record = self.metadata(f'repos/{slug}/license?ref={quote(tag, safe="")}')
                    candidate = candidate_for(spec, release, license_record, self.db.data_dir)
                    for artifact in candidate.assets.values():
                        self.downloader.download_artifact(artifact, output / 'downloads' / candidate.dependency_id / artifact.asset_name)
                    candidates.append(candidate)
                    report['state'] = 'verified-candidate'
            except Exception as exc:
                report.update(state='blocked', detail=type(exc).__name__ + ': candidate metadata, license or acquisition needs review')
            reports.append(report)
        # Tool watchers derive authoritative repositories from existing tool pins.
        tools = yaml.safe_load((self.db.data_dir / 'toolchains/catalog.yaml').read_text())
        tool_sources: dict[str, set[str]] = {}
        for tool in tools['toolchains']:
            for value in tool.values():
                if isinstance(value, dict) and value.get('source_url'):
                    parsed = urlparse(value['source_url'])
                    if parsed.hostname == 'github.com':
                        parts = parsed.path.strip('/').split('/')
                        if len(parts) >= 6 and parts[2:4] == ['releases', 'download']:
                            tool_sources.setdefault('/'.join(parts[:2]), set()).add(parts[4])
        for slug, pins in sorted(tool_sources.items()):
            try:
                if slug not in releases:
                    releases[slug] = self.metadata(f'repos/{slug}/releases/latest')
                latest = releases[slug]['tag_name']
                reports.append(dict(tool_repository=slug, pinned=sorted(pins), latest=latest,
                    state='unchanged' if latest in pins else 'migration-required', detail='Tool hashes, versions, licenses and host behavior require separate migration review.'))
            except Exception:
                reports.append(dict(tool_repository=slug, state='blocked', detail='Authoritative tool release metadata unavailable.'))
        proposal = ''
        # One independent candidate per proposal avoids silently combining changes.
        if candidates:
            candidate = candidates[0]
            proposed = deepcopy(catalog)
            for spec in proposed['dependencies']:
                if spec['id'] == candidate.dependency_id:
                    spec.update(version=candidate.tag.removeprefix('v'), release_tag=candidate.tag, date_verified=date.today().isoformat(),
                        artifacts={key: {k: v for k, v in artifact.to_dict().items() if k != 'variant'} for key, artifact in candidate.assets.items()})
            proposal_id = canonical_json_digest({'baseline': canonical_json_digest(catalog), 'dependency': candidate.dependency_id,
                'tag': candidate.tag, 'assets': {k: a.to_dict() for k, a in candidate.assets.items()}})
            proposed['macloader_dependency_set'] = 'candidate-' + proposal_id[:12]
            digest = canonical_json_digest(proposed)
            destination = output / 'candidates' / proposal_id
            destination.mkdir(parents=True, exist_ok=True)
            # Retain the catalog's actual version field, with candidate provenance.
            (destination / 'catalog.yaml').write_text(yaml.safe_dump(proposed, sort_keys=False))
            (destination / 'candidate.json').write_text(json.dumps(dict(schema_version='1', catalog_digest=digest,
                dependency=candidate.dependency_id, license_digest=candidate.license_digest, gates='pending', production_policy_changed=False), indent=2))
            proposal = str(destination)
        report = dict(schema_version='1', reports=reports, candidate_directory=proposal,
            proposal_ready=False, required_gates=['dependency-graph', 'verified-acquisition', 'deterministic-efi', 'matching-ocvalidate', 'matrix-tests-types-coverage', 'wheel-sdist'],
            production_policy_changed=False, auto_merge=False)
        output.mkdir(parents=True, exist_ok=True)
        (output / 'report.json').write_text(json.dumps(report, indent=2))
        (output / 'report.md').write_text('# Upstream candidate review\n\nProduction policy remains pinned. No auto-merge or hardware qualification.\n\n' + '\n'.join(f"- {r.get('dependency', r.get('tool_repository'))}: {r['state']} (latest {r.get('latest', 'unknown')})" for r in reports) + '\n\nCandidate readiness requires every workflow gate to pass.\n')
        return report


def overlay(candidate: Path, destination: Path, db: Database | None = None) -> Path:
    import shutil
    source = db or Database()
    raw = json.loads((candidate / 'candidate.json').read_text())
    catalog = yaml.safe_load((candidate / 'catalog.yaml').read_text())
    if raw['schema_version'] != '1' or raw['catalog_digest'] != canonical_json_digest(catalog):
        raise ValueError('Candidate catalog provenance changed')
    shutil.copytree(source.data_dir, destination)
    shutil.copy2(candidate / 'catalog.yaml', destination / 'dependencies/catalog.yaml')
    validated = Database(destination)
    from macloader.dependencies.graph import DependencyGraph
    graph = DependencyGraph()
    for spec in validated.list_dependency_specs():
        graph.add_node(spec.id, spec.dependencies)
    graph.resolve_ordered_set([spec.id for spec in validated.list_dependency_specs()])
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description='Report upstream candidates without modifying production catalogs')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = UpstreamUpdater().watch(args.output)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
