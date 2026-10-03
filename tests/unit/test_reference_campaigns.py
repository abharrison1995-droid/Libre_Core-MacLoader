"""Reference composition rejects drift and never expands machine eligibility."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest
import yaml

from macloader.database.loader import Database, DEFAULT_DATA_DIR
from macloader.domain.hardware import HardwareSnapshot
from macloader.exceptions import DatabaseValidationError


def _copy_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    shutil.copytree(DEFAULT_DATA_DIR, root)
    return root


def _candidate(fixture: Path) -> HardwareSnapshot:
    snapshot = HardwareSnapshot.from_dict(json.loads(fixture.read_text()))
    return replace(snapshot, machine_type="20L8", product_name="20L8CTO1WW", bios_version="N22ET85W (1.62 )")


def test_campaign_links_reviewed_records(db: Database) -> None:
    campaign = next(iter(db.campaigns.values()))
    assert campaign.release.build == "24A335"
    assert campaign.configuration_policy.options["profile.audio"].default_value == f"layout-{campaign.profile.audio.layout_id}"
    assert campaign.support_state == "EXPERIMENTAL"
    assert campaign.profile.usb.first_install_route == "SS01"
    assert len(campaign.digest) == 64


@pytest.mark.parametrize("change", [
    {"machine_type": "20L7"}, {"bios_version": "N22ET76W (1.53 )"},
    {"manufacturer": "Dell"}, {"machine_type": None}, {"bios_version": None},
])
def test_wrong_machine_or_bios_cannot_inherit_campaign(
    db: Database, t480s_baseline_fixture: Path, change: dict[str, object],
) -> None:
    candidate = _candidate(t480s_baseline_fixture)
    assert db.candidate_campaign(candidate) is not None
    assert db.candidate_campaign(replace(candidate, **change)) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value", [
    ("reviewed_profile_id", "missing"), ("configuration_policy_id", "missing"),
    ("evidence_policy_id", "missing"), ("recovery_policy_id", "missing"),
    ("bios_binding", "N22ET76W-1.53"), ("model_id", "missing"),
    ("required_components", {"wifi": ["missing"]}),
    ("target", {"product_id": "sequoia", "version": "15.1", "build": "other"}),
])
def test_campaign_dangling_or_conflicting_reference_rejected(tmp_path: Path, field: str, value: object) -> None:
    root = _copy_catalog(tmp_path)
    path = next((root / "campaigns").glob("*.yaml"))
    data = yaml.safe_load(path.read_text())
    data[field] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(DatabaseValidationError):
        Database(root)


def test_campaign_duplicate_selector_rejected(tmp_path: Path) -> None:
    root = _copy_catalog(tmp_path)
    path = next((root / "campaigns").glob("*.yaml"))
    data = yaml.safe_load(path.read_text())
    data["campaign_id"] = "competing-campaign"
    (path.parent / "duplicate.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(DatabaseValidationError, match="eligibility"):
        Database(root)


def test_policy_audio_drift_fails_closed(tmp_path: Path) -> None:
    root = _copy_catalog(tmp_path)
    path = root / "configuration" / "t480s.yaml"
    data = yaml.safe_load(path.read_text())
    next(option for option in data["options"] if option["id"] == "profile.audio")["default"] = "layout-11"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(DatabaseValidationError, match="audio default"):
        Database(root)
