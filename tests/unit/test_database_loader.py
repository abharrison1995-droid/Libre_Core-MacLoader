"""Unit tests for declarative database loader and schema validation."""

from pathlib import Path
import pytest

from macloader.database.loader import Database, get_database
from macloader.database.schema import ComponentSchema, ModelSchema, MacOsSchema
from macloader.domain.compatibility import CompatibilityState
from macloader.exceptions import DatabaseNotFoundError, DatabaseValidationError


def test_database_loads_all_standard_definitions(db: Database) -> None:
    # Models
    assert "thinkpad-t480s" in db.models
    assert "thinkpad-t480" in db.models

    t480s = db.get_model("thinkpad-t480s")
    assert t480s is not None
    assert "20L7" in t480s.machine_types
    assert "20L8" in t480s.machine_types

    t480 = db.get_model("thinkpad-t480")
    assert t480 is not None
    assert "20L5" in t480.machine_types
    assert "20L6" in t480.machine_types

    # Components
    assert "intel-uhd-620" in db.components
    assert "nvidia-geforce-mx150" in db.components
    assert "realtek-alc257" in db.components
    assert "samsung-pm981" in db.components
    assert "intel-ac-8265" in db.components

    # macOS targets
    assert db.get_macos("sonoma") is not None
    assert db.get_macos("sequoia") is not None
    assert db.get_macos("tahoe") is not None


def test_schema_validation_fails_on_missing_model_fields() -> None:
    invalid_data = {
        "id": "broken-model",
        "vendor": "LENOVO",
        # Missing required fields like machine_types, product_names
    }
    with pytest.raises(DatabaseValidationError, match="Missing required field"):
        ModelSchema.validate_and_load(invalid_data, filename="broken.yaml")


def test_schema_validation_fails_on_invalid_compatibility_state() -> None:
    invalid_data = {
        "id": "broken-comp",
        "category": "graphics",
        "name": "Broken Graphics",
        "match_rules": {"pci_ids": ["8086:9999"]},
        "macos_policies": {
            "sequoia": {
                "state": "SUPER_SUPPORTED_100_PERCENT",  # Invalid state enum
                "reason": "Invalid",
            }
        },
    }
    with pytest.raises(DatabaseValidationError, match="Invalid compatibility state"):
        ComponentSchema.validate_and_load(invalid_data, filename="broken.yaml")


def test_custom_database_registry_does_not_replace_default(tmp_path: Path) -> None:
    default = get_database()
    custom = get_database(tmp_path)
    assert custom is not default
    assert get_database() is default
