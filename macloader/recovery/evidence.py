"""Current Recovery discovery evidence and the readiness it can support.

MacLoader has no Apple-authoritative, authenticated route that binds an Apple
Recovery product or payload to build 24A335 (see
``docs/RECOVERY_BUILD_BINDING_DECISION.md``).  Discovery outcomes are therefore
recorded only so that preflight can report *current* observations instead of
a fixed historical message.  A record is local, unauthenticated data: it can
explain why Recovery is blocked, but no record can make Recovery ready.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping, Optional

from macloader.domain.contracts import canonical_json_digest
from macloader.domain.recovery import RecoveryState
from macloader.recovery.discovery import RecoveryDiscoveryResult


RECOVERY_EVIDENCE_SCHEMA = "1"
MAX_RECOVERY_EVIDENCE_AGE = timedelta(days=7)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_DIAGNOSTICS = 4
MAX_DIAGNOSTIC_CHARS = 240
DECISION_RECORD = "docs/RECOVERY_BUILD_BINDING_DECISION.md"

_OUTCOMES = {"response", "transport_error"}
_KEYS = {
    "schema_version", "observed_at", "policy_digest", "target_digest", "outcome", "state",
    "diagnostics", "apple_product_id", "record_digest", "integrity_digest",
}
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_PRODUCT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SECRET_RE = re.compile(r"(?i)(session=|assettoken=|cookie|https?://)\S*")


def _clean_diagnostic(text: str) -> str:
    """Bound one diagnostic and drop tokens, cookies and URLs."""
    printable = "".join(character if " " <= character <= "~" else " " for character in str(text))
    return _SECRET_RE.sub("<redacted>", printable).strip()[:MAX_DIAGNOSTIC_CHARS]


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RecoveryDiscoveryEvidence:
    """One redacted observation of the pinned Apple discovery exchange."""

    observed_at: str
    policy_digest: str
    target_digest: str
    outcome: str
    state: RecoveryState
    diagnostics: tuple[str, ...]
    apple_product_id: Optional[str] = None
    record_digest: Optional[str] = None
    schema_version: str = RECOVERY_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != RECOVERY_EVIDENCE_SCHEMA:
            raise ValueError("Recovery evidence schema is unsupported")
        if not _TIMESTAMP_RE.fullmatch(self.observed_at):
            raise ValueError("Recovery evidence timestamp is malformed")
        if not _DIGEST_RE.fullmatch(self.policy_digest) or not _DIGEST_RE.fullmatch(self.target_digest):
            raise ValueError("Recovery evidence policy or target digest is malformed")
        if self.outcome not in _OUTCOMES:
            raise ValueError("Recovery evidence outcome is unsupported")
        if self.record_digest is not None and not _DIGEST_RE.fullmatch(self.record_digest):
            raise ValueError("Recovery evidence record digest is malformed")
        if self.apple_product_id is not None and not _PRODUCT_RE.fullmatch(self.apple_product_id):
            raise ValueError("Recovery evidence product identifier is malformed")
        if (
            len(self.diagnostics) > MAX_DIAGNOSTICS
            or any(not isinstance(item, str) or item != _clean_diagnostic(item) for item in self.diagnostics)
        ):
            raise ValueError("Recovery evidence diagnostics are unbounded or unredacted")
        if self.outcome == "transport_error" and (
            self.state != RecoveryState.FAILED or self.record_digest is not None or self.apple_product_id is not None
        ):
            raise ValueError("Recovery transport-error evidence is inconsistent")

    @classmethod
    def from_result(
        cls, result: RecoveryDiscoveryResult, policy_digest: str, now: Optional[datetime] = None
    ) -> "RecoveryDiscoveryEvidence":
        return cls(
            observed_at=_timestamp(now or datetime.now(timezone.utc)),
            policy_digest=policy_digest.lower(),
            target_digest=result.target.digest.lower(),
            outcome="response",
            state=result.state,
            diagnostics=tuple(_clean_diagnostic(item) for item in result.diagnostics[:MAX_DIAGNOSTICS]),
            apple_product_id=result.apple_product_id,
            record_digest=result.record_digest.lower(),
        )

    @classmethod
    def from_error(
        cls, error: BaseException, policy_digest: str, target_digest: str, now: Optional[datetime] = None
    ) -> "RecoveryDiscoveryEvidence":
        return cls(
            observed_at=_timestamp(now or datetime.now(timezone.utc)),
            policy_digest=policy_digest.lower(),
            target_digest=target_digest.lower(),
            outcome="transport_error",
            state=RecoveryState.FAILED,
            diagnostics=(_clean_diagnostic(str(error) or type(error).__name__),),
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observed_at": self.observed_at,
            "policy_digest": self.policy_digest,
            "target_digest": self.target_digest,
            "outcome": self.outcome,
            "state": self.state.value,
            "diagnostics": list(self.diagnostics),
            "apple_product_id": self.apple_product_id,
            "record_digest": self.record_digest,
        }

    @property
    def integrity_digest(self) -> str:
        return canonical_json_digest(self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "integrity_digest": self.integrity_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecoveryDiscoveryEvidence":
        if not isinstance(data, Mapping) or set(data) != _KEYS:
            raise ValueError("Recovery evidence fields are missing or unexpected")
        diagnostics = data["diagnostics"]
        if not isinstance(diagnostics, list):
            raise ValueError("Recovery evidence diagnostics must be a list")
        for key in ("schema_version", "observed_at", "policy_digest", "target_digest", "outcome", "state", "integrity_digest"):
            if not isinstance(data[key], str):
                raise ValueError("Recovery evidence field has an invalid type")
        for key in ("apple_product_id", "record_digest"):
            if data[key] is not None and not isinstance(data[key], str):
                raise ValueError("Recovery evidence field has an invalid type")
        evidence = cls(
            observed_at=data["observed_at"],
            policy_digest=data["policy_digest"],
            target_digest=data["target_digest"],
            outcome=data["outcome"],
            state=RecoveryState(data["state"]),
            diagnostics=tuple(diagnostics),
            apple_product_id=data["apple_product_id"],
            record_digest=data["record_digest"],
            schema_version=data["schema_version"],
        )
        if data["integrity_digest"] != evidence.integrity_digest:
            raise ValueError("Recovery evidence integrity digest does not match its contents")
        return evidence

    def observed_datetime(self) -> datetime:
        return datetime.strptime(self.observed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RecoveryReadiness:
    state: str
    summary: str
    action: str


_RESOLVE_ACTION = (
    "Run `macloader recovery resolve` to record current Apple discovery evidence. "
    f"Acquisition stays blocked until an authenticated 24A335 binding exists; see {DECISION_RECORD}."
)
_BLOCKED_ACTION = (
    "No change on this machine can close this gate: Apple must provide an authenticated HTTPS relationship "
    "between a Recovery product or signed payload and build 24A335. "
    f"Re-run `macloader recovery resolve` to refresh the evidence; see {DECISION_RECORD}."
)


def assess_recovery_evidence(
    data: Optional[Any],
    *,
    policy_digest: str,
    target_digest: str,
    now: Optional[datetime] = None,
    load_error: Optional[str] = None,
) -> RecoveryReadiness:
    """Derive the preflight Recovery check from current recorded evidence.

    The result is never ``ready``: no exact-build binding route exists, so a
    record claiming an exact product is rejected as inconsistent rather than
    trusted.
    """
    if load_error is not None:
        return RecoveryReadiness(
            "blocked",
            f"Recorded Recovery discovery evidence is unusable ({load_error}); it was ignored.",
            _RESOLVE_ACTION,
        )
    if data is None:
        return RecoveryReadiness(
            "missing", "No Recovery discovery evidence has been recorded in this workspace.", _RESOLVE_ACTION
        )
    try:
        evidence = RecoveryDiscoveryEvidence.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        return RecoveryReadiness(
            "blocked",
            f"Recorded Recovery discovery evidence is malformed or was modified ({exc}); it was ignored.",
            _RESOLVE_ACTION,
        )
    current = now or datetime.now(timezone.utc)
    observed = evidence.observed_datetime()
    if observed > current + MAX_CLOCK_SKEW:
        return RecoveryReadiness(
            "blocked", "Recorded Recovery discovery evidence is dated in the future; it was ignored.", _RESOLVE_ACTION
        )
    if evidence.policy_digest != policy_digest.lower() or evidence.target_digest != target_digest.lower():
        return RecoveryReadiness(
            "stale",
            "Recorded Recovery discovery evidence belongs to a different Recovery policy or target.",
            _RESOLVE_ACTION,
        )
    if current - observed > MAX_RECOVERY_EVIDENCE_AGE:
        return RecoveryReadiness(
            "stale",
            f"Recorded Recovery discovery evidence from {evidence.observed_at} is older than "
            f"{MAX_RECOVERY_EVIDENCE_AGE.days} days.",
            _RESOLVE_ACTION,
        )
    when = evidence.observed_at
    detail = "; ".join(evidence.diagnostics)
    if evidence.state == RecoveryState.AMBIGUOUS and evidence.apple_product_id:
        return RecoveryReadiness(
            "externally_blocked",
            f"Apple discovery at {when} returned Recovery product {evidence.apple_product_id} with no authenticated "
            "version/build metadata; build 24A335 is unproven.",
            _BLOCKED_ACTION,
        )
    if evidence.state == RecoveryState.UNAVAILABLE:
        return RecoveryReadiness(
            "externally_blocked", f"Apple discovery at {when} reported the Recovery product unavailable.", _BLOCKED_ACTION
        )
    if evidence.state == RecoveryState.FAILED:
        return RecoveryReadiness(
            "externally_blocked",
            f"Apple discovery at {when} failed: {detail or 'no diagnostic was recorded'}.",
            _BLOCKED_ACTION,
        )
    return RecoveryReadiness(
        "blocked",
        f"Recorded Recovery discovery state '{evidence.state.value}' cannot come from the pinned discovery client, "
        "which has no authenticated build-binding route; the record was rejected.",
        _RESOLVE_ACTION,
    )
