"""Private SMBIOS identity lifecycle guards."""

from pathlib import Path
import os
import subprocess
from unittest import mock

import pytest

from macloader.domain.contracts import IdentityReference
from macloader.identity.service import IdentityService, IdentityServiceError
import macloader.identity.service as identity_module


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


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda values: values.pop("ROM"), "missing or unsupported"),
        (lambda values: values.update(SystemProductName="MacBookPro16,1"), "model"),
        (lambda values: values.update(SystemSerialNumber="bad value"), "SystemSerialNumber"),
        (lambda values: values.update(MLB="x"), "MLB"),
        (lambda values: values.update(SystemUUID="not-a-uuid"), "SystemUUID"),
        (lambda values: values.update(ROM="not-hex"), "ROM"),
    ],
)
def test_identity_validation_rejects_malformed_private_values(mutator: object, message: str) -> None:
    values = IdentityService.fake_identity()
    mutator(values)  # type: ignore[operator]
    with pytest.raises(IdentityServiceError, match=message):
        IdentityService.validate(values)


def test_identity_generation_requires_trusted_tool_and_handles_tool_failures(tmp_path: Path) -> None:
    service = IdentityService(tmp_path)
    with pytest.raises(IdentityServiceError, match="Trusted macserial"):
        service.generate(allow_real=True)
    tool = tmp_path / "macserial"
    tool.write_text("placeholder", encoding="utf-8")
    service = IdentityService(tmp_path, tool)

    with mock.patch.object(identity_module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
        with pytest.raises(IdentityServiceError, match="failure status"):
            service.generate(allow_real=True)
    with mock.patch.object(identity_module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "no pair", "")):
        with pytest.raises(IdentityServiceError, match="no usable"):
            service.generate(allow_real=True)
    with mock.patch.object(
        identity_module.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired([str(tool)], 20),
    ):
        with pytest.raises(IdentityServiceError, match="tool failed"):
            service.generate(allow_real=True)
    with mock.patch.object(identity_module.subprocess, "run", side_effect=OSError("missing")):
        with pytest.raises(IdentityServiceError, match="tool failed"):
            service.generate(allow_real=True)

    completed = subprocess.CompletedProcess([], 0, "SERIAL123|MLB12345678901234\n", "")
    with mock.patch.object(identity_module.subprocess, "run", return_value=completed) as run:
        values = service.generate(allow_real=True)
    assert values["SystemProductName"] == "MacBookPro15,2"
    run.assert_called_once()


def test_identity_storage_and_reuse_fail_closed(tmp_path: Path) -> None:
    service = IdentityService(tmp_path)
    values = service.fake_identity()
    with pytest.raises(IdentityServiceError, match="simple JSON filename"):
        service.store(values, storage_ref="../identity.json")
    with pytest.raises(IdentityServiceError, match="simple JSON filename"):
        service.store(values, storage_ref="identity.txt")

    link = tmp_path / "link.json"
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this host")
    with pytest.raises(IdentityServiceError, match="must not be a symlink"):
        service.store(values, storage_ref="link.json")

    with pytest.raises(IdentityServiceError, match="redacted"):
        service.reuse(IdentityReference("0.1", "missing.json", redacted=False))
    with pytest.raises(IdentityServiceError, match="missing or unsafe"):
        service.reuse(IdentityReference("0.1", "missing.json", redacted=True))
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(IdentityServiceError, match="cannot be read"):
        service.reuse(IdentityReference("0.1", "malformed.json", redacted=True))
    wrong_shape = tmp_path / "wrong-shape.json"
    wrong_shape.write_text("[]", encoding="utf-8")
    with pytest.raises(IdentityServiceError, match="invalid shape"):
        service.reuse(IdentityReference("0.1", "wrong-shape.json", redacted=True))
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"SystemProductName": "not-a-mac"}', encoding="utf-8")
    with pytest.raises(IdentityServiceError, match="missing or unsupported|model"):
        service.reuse(IdentityReference("0.1", "invalid.json", redacted=True))
    assert IdentityService.redact("other value", {"empty": "", "other": "value"}) == "other <redacted>"
