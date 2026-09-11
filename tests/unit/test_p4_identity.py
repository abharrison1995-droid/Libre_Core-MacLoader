"""Private SMBIOS identity lifecycle guards."""

from pathlib import Path
import os

import pytest

from macloader.domain.contracts import IdentityReference
from macloader.identity.service import IdentityService, IdentityServiceError


def test_fake_identity_is_explicitly_test_only_and_round_trips_privately(tmp_path: Path) -> None:
    service = IdentityService(tmp_path)
    values = service.fake_identity()
    with pytest.raises(IdentityServiceError, match="explicit private-workflow"):
        service.generate()
    stored = service.store(values)
    assert stored.storage_ref.endswith(".json")
    assert stored.storage_ref not in values.values()
    if os.name != "nt":
        assert (tmp_path / stored.storage_ref).stat().st_mode & 0o077 == 0
    reused = service.reuse(IdentityReference("0.1", stored.storage_ref, redacted=True))
    assert reused.values == values
    assert service.redact("identity=" + values["SystemSerialNumber"], values).endswith("<redacted>")


def test_identity_rejects_unsafe_reference_and_wrong_model(tmp_path: Path) -> None:
    service = IdentityService(tmp_path)
    values = service.fake_identity()
    values["SystemProductName"] = "MacBookPro16,1"
    with pytest.raises(IdentityServiceError, match="model"):
        service.validate(values)
    with pytest.raises(IdentityServiceError, match="filename"):
        service.reuse(IdentityReference("0.1", "../identity.json", redacted=True))
