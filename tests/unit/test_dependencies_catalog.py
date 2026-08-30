"""Unit tests for the declarative dependency catalog and schema validation."""

import pytest
from macloader.database.loader import Database
from macloader.database.schema import DependencyCatalogSchema
from macloader.domain.dependencies import ArtifactVariant
from macloader.exceptions import DatabaseValidationError


def test_dependency_catalog_loads_from_database(test_db: Database) -> None:
    catalog = test_db.get_dependency_catalog()
    assert catalog is not None
    assert catalog.policy_version == "2026.08-a"
    assert len(catalog.dependencies) >= 10

    # Verify OpenCore spec
    oc = test_db.get_dependency_spec("opencore")
    assert oc is not None
    assert oc.version == "1.0.7"
    rel_art = oc.get_artifact(ArtifactVariant.RELEASE)
    assert rel_art is not None
    assert oc.get_artifact(ArtifactVariant.DEBUG) is not None
    assert len(rel_art.sha256) == 64

    # Verify Lilu spec
    lilu = test_db.get_dependency_spec("lilu")
    assert lilu is not None
    assert lilu.version == "1.7.2"
    assert lilu.dependencies == []

    # Verify WhateverGreen depends on Lilu
    weg = test_db.get_dependency_spec("whatevergreen")
    assert weg is not None
    assert weg.version == "1.7.0"
    assert "lilu" in weg.dependencies

    # Verify BlueToolFixup provenance from BrcmPatchRAM
    bt = test_db.get_dependency_spec("bluetoolfixup")
    assert bt is not None
    assert "BrcmPatchRAM" in bt.project_name
    assert "lilu" in bt.dependencies


def test_catalog_schema_fails_on_missing_fields() -> None:
    invalid_data = {
        "macloader_dependency_set": "2026.08-a",
        "dependencies": [
            {
                "id": "bad_dep",
                # Missing project_name, upstream_repository, license, etc.
            }
        ],
    }
    with pytest.raises(DatabaseValidationError) as exc:
        DependencyCatalogSchema.validate_and_load(invalid_data, filename="test.yaml")
    assert "Missing required field" in str(exc.value)


def test_catalog_schema_fails_on_invalid_sha256() -> None:
    invalid_data = {
        "macloader_dependency_set": "2026.08-a",
        "dependencies": [
            {
                "id": "bad_hash_dep",
                "project_name": "Test",
                "upstream_repository": "https://example.com/test",
                "license": "MIT",
                "version": "1.0.0",
                "release_tag": "1.0.0",
                "artifacts": {
                    "RELEASE": {
                        "asset_name": "test.zip",
                        "source_url": "https://example.com/test.zip",
                        "sha256": "not_a_valid_64_char_sha256",
                    }
                },
            }
        ],
    }
    with pytest.raises(DatabaseValidationError) as exc:
        DependencyCatalogSchema.validate_and_load(invalid_data, filename="test.yaml")
    assert "Invalid SHA-256 hash" in str(exc.value)


def test_catalog_schema_fails_on_duplicate_dependency_id() -> None:
    duplicate_data = {
        "macloader_dependency_set": "2026.08-a",
        "dependencies": [
            {
                "id": "dup_dep",
                "project_name": "Test1",
                "upstream_repository": "https://example.com/test",
                "license": "MIT",
                "version": "1.0.0",
                "release_tag": "1.0.0",
                "artifacts": {
                    "RELEASE": {
                        "asset_name": "test1.zip",
                        "source_url": "https://example.com/test1.zip",
                        "sha256": "a" * 64,
                    }
                },
            },
            {
                "id": "dup_dep",
                "project_name": "Test2",
                "upstream_repository": "https://example.com/test",
                "license": "MIT",
                "version": "1.0.0",
                "release_tag": "1.0.0",
                "artifacts": {
                    "RELEASE": {
                        "asset_name": "test2.zip",
                        "source_url": "https://example.com/test2.zip",
                        "sha256": "b" * 64,
                    }
                },
            },
        ],
    }
    with pytest.raises(DatabaseValidationError) as exc:
        DependencyCatalogSchema.validate_and_load(duplicate_data, filename="test.yaml")
    assert "Duplicate dependency ID" in str(exc.value)
