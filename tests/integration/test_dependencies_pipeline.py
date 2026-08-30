"""Integration tests covering end-to-end BuildPlan to dependency resolution, mock fetching, and cache verification."""

import hashlib
from pathlib import Path
from macloader.orchestrator import Orchestrator
from macloader.domain.dependencies import ArtifactVariant


def test_full_dependency_pipeline_with_mock_downloads(tmp_path: Path, t480s_baseline_fixture: Path) -> None:
    cache_dir = tmp_path / "test_cache"
    orchestrator = Orchestrator(cache_dir=cache_dir)

    # 1. Probe
    snapshot = orchestrator.probe_hardware(fixture_path=t480s_baseline_fixture)
    # 2. Plan
    plan = orchestrator.generate_plan(snapshot, target_macos="sequoia")
    # 3. Resolve
    dep_set = orchestrator.resolve_dependencies(plan, variant=ArtifactVariant.RELEASE)

    assert dep_set.target_model == "Lenovo ThinkPad T480s"
    assert len(dep_set.resolved_dependencies) >= 6

    # 4. Mock Fetch into cache
    # Setup mock transport that writes bytes matching expected SHA-256 for test artifacts
    def mock_fetch(url: str, dest_path: Path) -> None:
        # Find which artifact this is
        for dep in dep_set.resolved_dependencies:
            if dep.artifact.source_url == url:
                # We write dummy content and adjust hash if needed, or create content matching spec
                dest_path.write_bytes(b"dummy")
                # For this test, we temporarily mock sha check or create exact match
                break

    # To test fetch with real validation, let's create valid payloads for resolved specs:
    for dep in dep_set.resolved_dependencies:
        spec = orchestrator.db.get_dependency_spec(dep.dependency_id)
        if spec:
            art = spec.get_artifact(dep.variant)
            if art:
                # Create a small valid file matching art.sha256
                dummy_content = f"mock content for {dep.dependency_id}".encode("utf-8")
                art.sha256 = hashlib.sha256(dummy_content).hexdigest()

    def mock_matching_transport(url: str, dest_path: Path) -> None:
        for dep in dep_set.resolved_dependencies:
            if dep.artifact.source_url == url:
                dest_path.write_bytes(f"mock content for {dep.dependency_id}".encode("utf-8"))
                return
        dest_path.write_bytes(b"fallback")

    results = orchestrator.fetch_dependencies(dep_set, transport=mock_matching_transport)
    assert len(results) == len(dep_set.resolved_dependencies)

    # 5. Verify cached dependencies
    status = orchestrator.verify_cached_dependencies(dep_set)
    assert all(status.values()) is True

    # 6. Fetch in offline mode should now succeed completely from cache without network
    offline_results = orchestrator.fetch_dependencies(dep_set, offline=True)
    assert len(offline_results) == len(dep_set.resolved_dependencies)
