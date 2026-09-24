"""Recovery discovery evidence: preflight derives state from current records only.

No record can make the exact-Recovery gate ready, because no authenticated
product-to-build route exists.  Forged, stale and mismatched records must be
rejected or reported as stale rather than trusted.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable

from click.testing import CliRunner
import pytest

from macloader.domain.recovery import RecoveryState, RecoveryTarget
from macloader.exceptions import ArtifactDownloadError
from macloader.orchestrator import Orchestrator
from macloader.recovery.discovery import RecoveryDiscoveryResult
from macloader.recovery.evidence import (
    MAX_RECOVERY_EVIDENCE_AGE,
    RecoveryDiscoveryEvidence,
    assess_recovery_evidence,
)
from macloader.recovery.service import load_recovery_policy
from macloader.ui.cli import cli
from macloader.workflow.service import WorkflowService


NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
POLICY = load_recovery_policy()
TARGET = RecoveryTarget("sequoia", "macOS Sequoia", "15.0", "24A335")


def _result(state: RecoveryState, product: str | None = "696-28424", diagnostic: str = "no build metadata") -> RecoveryDiscoveryResult:
    return RecoveryDiscoveryResult(state, TARGET, None, "a" * 64, (diagnostic,), product)


def _record(state: RecoveryState = RecoveryState.AMBIGUOUS, when: datetime = NOW, **overrides: Any) -> dict[str, Any]:
    evidence = RecoveryDiscoveryEvidence.from_result(_result(state), POLICY.digest, when)
    data = evidence.to_dict()
    data.update(overrides)
    return data


def _assess(data: Any, now: datetime = NOW, **kwargs: Any) -> Any:
    return assess_recovery_evidence(
        data, policy_digest=POLICY.digest, target_digest=POLICY.target.digest, now=now, **kwargs
    )


def _reseal(data: dict[str, Any]) -> dict[str, Any]:
    """Model a forger who also recomputes the (unkeyed) integrity digest."""
    body = {key: value for key, value in data.items() if key != "integrity_digest"}
    body["state"] = RecoveryState(body["state"])
    body["diagnostics"] = tuple(body["diagnostics"])
    return RecoveryDiscoveryEvidence(**body).to_dict()


def test_missing_evidence_is_missing_with_actionable_next_step() -> None:
    readiness = _assess(None)
    assert readiness.state == "missing"
    assert "macloader recovery resolve" in readiness.action
    assert "RECOVERY_BUILD_BINDING_DECISION" in readiness.action


def test_current_ambiguous_product_is_reported_from_evidence_not_history() -> None:
    readiness = _assess(_record())
    assert readiness.state == "externally_blocked"
    assert "696-28424" in readiness.summary and "2026-09-24T12:00:00Z" in readiness.summary
    assert "405" not in readiness.summary


def test_transport_failure_reports_the_current_diagnostic() -> None:
    error = ArtifactDownloadError("Recovery discovery failed: HTTPS returned HTTP 503")
    data = RecoveryDiscoveryEvidence.from_error(error, POLICY.digest, POLICY.target.digest, NOW).to_dict()
    readiness = _assess(data)
    assert readiness.state == "externally_blocked"
    assert "HTTP 503" in readiness.summary


def test_unavailable_is_externally_blocked() -> None:
    readiness = _assess(_record(RecoveryState.UNAVAILABLE))
    assert readiness.state == "externally_blocked" and "unavailable" in readiness.summary


@pytest.mark.parametrize("state", [RecoveryState.DISCOVERED, RecoveryState.VERIFIED, RecoveryState.LOCKED])
def test_forged_exact_product_claims_are_rejected_even_when_resealed(state: RecoveryState) -> None:
    data = _record()
    data["state"] = state.value
    forged = _reseal(data)
    readiness = _assess(forged)
    assert readiness.state == "blocked"
    assert "rejected" in readiness.summary


def test_ambiguous_without_product_identifier_is_rejected() -> None:
    forged = _reseal(_record(apple_product_id=None))
    assert _assess(forged).state == "blocked"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda data: data.update(state="discovered"), id="state-edit-without-reseal"),
        pytest.param(lambda data: data.update(integrity_digest="0" * 64), id="bad-integrity"),
        pytest.param(lambda data: data.update(extra="field"), id="unexpected-field"),
        pytest.param(lambda data: data.pop("observed_at"), id="missing-field"),
        pytest.param(lambda data: data.update(observed_at="yesterday"), id="bad-timestamp"),
        pytest.param(lambda data: data.update(diagnostics="text"), id="diagnostics-type"),
        pytest.param(lambda data: data.update(diagnostics=["session=SECRET"]), id="unredacted-diagnostic"),
        pytest.param(lambda data: data.update(apple_product_id="../etc"), id="bad-product"),
        pytest.param(lambda data: data.update(record_digest="xyz"), id="bad-record-digest"),
        pytest.param(lambda data: data.update(schema_version="2"), id="schema"),
        pytest.param(lambda data: data.update(outcome="guess"), id="outcome"),
        pytest.param(lambda data: data.update(state="not-a-state"), id="unknown-state"),
        pytest.param(lambda data: data.update(policy_digest=5), id="policy-type"),
        pytest.param(lambda data: data.update(apple_product_id=5), id="product-type"),
    ],
)
def test_forged_or_corrupted_records_are_ignored(mutate: Callable[[dict[str, Any]], None]) -> None:
    data = _record()
    mutate(data)
    readiness = _assess(data)
    assert readiness.state == "blocked"
    assert "ignored" in readiness.summary


def test_non_mapping_and_inconsistent_transport_records_are_ignored() -> None:
    assert _assess(["not", "a", "record"]).state == "blocked"
    error_record = RecoveryDiscoveryEvidence.from_error(OSError("x"), POLICY.digest, POLICY.target.digest, NOW).to_dict()
    error_record["apple_product_id"] = "696-28424"
    assert _assess(error_record).state == "blocked"


def test_stale_records_are_reported_as_stale() -> None:
    old = _record(when=NOW - MAX_RECOVERY_EVIDENCE_AGE - timedelta(seconds=1))
    readiness = _assess(old)
    assert readiness.state == "stale" and "older than" in readiness.summary
    assert _assess(_record(when=NOW - MAX_RECOVERY_EVIDENCE_AGE)).state == "externally_blocked"


def test_future_dated_records_are_ignored() -> None:
    assert _assess(_reseal(_record(when=NOW + timedelta(hours=1)))).state == "blocked"
    assert _assess(_record(when=NOW + timedelta(minutes=4))).state == "externally_blocked"


@pytest.mark.parametrize(
    "field", ["policy_digest", "target_digest"],
)
def test_records_for_another_policy_or_target_are_stale(field: str) -> None:
    data = _record()
    data[field] = "f" * 64
    mismatched = _reseal(data)
    readiness = _assess(mismatched)
    assert readiness.state == "stale" and "different Recovery policy or target" in readiness.summary


def test_unreadable_evidence_is_reported_without_trusting_it() -> None:
    readiness = _assess(None, load_error="permissions are too broad")
    assert readiness.state == "blocked" and "permissions are too broad" in readiness.summary


def test_evidence_redacts_tokens_and_bounds_diagnostics() -> None:
    result = RecoveryDiscoveryResult(
        RecoveryState.FAILED, TARGET, None, "b" * 64,
        ("cookie session=abc AssetToken=xyz https://osrecovery.apple.com/x\\x00" + "y" * 500,) * 6,
    )
    evidence = RecoveryDiscoveryEvidence.from_result(result, POLICY.digest, NOW)
    assert len(evidence.diagnostics) == 4
    text = " ".join(evidence.diagnostics)
    assert "abc" not in text and "xyz" not in text and "apple.com" not in text
    assert all(len(item) <= 240 for item in evidence.diagnostics)


def test_workflow_records_current_discovery_and_preflight_uses_it(
    monkeypatch: pytest.MonkeyPatch, _isolated_recovery_evidence: Path
) -> None:
    service = WorkflowService()
    monkeypatch.setattr(Orchestrator, "discover_recovery", lambda _self, **_kwargs: _result(RecoveryState.AMBIGUOUS))
    service.discover_recovery()
    assert _isolated_recovery_evidence.is_file()
    if os.name != "nt":
        assert _isolated_recovery_evidence.stat().st_mode & 0o077 == 0
    stored = json.loads(_isolated_recovery_evidence.read_text(encoding="utf-8"))
    assert stored["apple_product_id"] == "696-28424" and stored["state"] == "ambiguous"
    readiness = service.recovery_readiness()
    assert readiness.state == "externally_blocked" and "696-28424" in readiness.summary

    report = service.preflight(None, None)
    exact = next(item for item in report["checks"] if item["id"] == "exact_recovery")
    assert exact["state"] == "externally_blocked" and "696-28424" in exact["summary"]
    assert report["status"] == "blocked"


def test_workflow_records_transport_errors_but_not_cancellations(
    monkeypatch: pytest.MonkeyPatch, _isolated_recovery_evidence: Path
) -> None:
    service = WorkflowService()

    def failing(_self: Orchestrator, **_kwargs: Any) -> RecoveryDiscoveryResult:
        raise ArtifactDownloadError("Recovery discovery failed: HTTPS returned HTTP 405")

    monkeypatch.setattr(Orchestrator, "discover_recovery", failing)
    with pytest.raises(ArtifactDownloadError):
        service.discover_recovery()
    assert "HTTP 405" in service.recovery_readiness().summary
    before = _isolated_recovery_evidence.read_bytes()

    def cancelled(_self: Orchestrator, **_kwargs: Any) -> RecoveryDiscoveryResult:
        raise ArtifactDownloadError("Recovery discovery cancelled")

    monkeypatch.setattr(Orchestrator, "discover_recovery", cancelled)
    with pytest.raises(ArtifactDownloadError):
        service.discover_recovery(cancel=lambda: False)
    monkeypatch.setattr(Orchestrator, "discover_recovery", lambda _self, **_kwargs: _result(RecoveryState.UNAVAILABLE))
    service.discover_recovery(cancel=lambda: True)
    assert _isolated_recovery_evidence.read_bytes() == before


def test_workflow_ignores_evidence_with_broad_permissions(
    monkeypatch: pytest.MonkeyPatch, _isolated_recovery_evidence: Path
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permission check")
    service = WorkflowService()
    monkeypatch.setattr(Orchestrator, "discover_recovery", lambda _self, **_kwargs: _result(RecoveryState.AMBIGUOUS))
    service.discover_recovery()
    _isolated_recovery_evidence.chmod(0o644)
    readiness = service.recovery_readiness()
    assert readiness.state == "blocked" and "ignored" in readiness.summary


def test_cli_resolve_and_status_show_evidence_derived_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Orchestrator, "discover_recovery", lambda _self: _result(RecoveryState.AMBIGUOUS))
    resolved = CliRunner().invoke(cli, ["recovery", "resolve"])
    assert resolved.exit_code != 0
    assert "no fallback" in resolved.output and "Preflight exact_recovery: externally_blocked" in resolved.output
    status = CliRunner().invoke(cli, ["recovery", "status", "--json"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["state"] == "externally_blocked" and "696-28424" in payload["summary"]
    human = CliRunner().invoke(cli, ["recovery", "status"])
    assert "Next action:" in human.output
