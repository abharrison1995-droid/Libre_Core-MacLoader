"""Pinned Apple Recovery discovery with explicit exact-target outcomes."""

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from macloader.domain.recovery import RecoveryProduct, RecoveryState, RecoveryTarget
from macloader.exceptions import ArtifactDownloadError


APPLE_RECOVERY_HOST = "osrecovery.apple.com"
APPLE_QUERY_SCHEME = "https"
OPENCORE_RECOVERY_TOOL_VERSION = "1.0.7"
SEQUOIA_BOARD_ID = "Mac-7BA5B2D9E42DDD94"
ZERO_MLB = "00000000000000000"
MAX_DISCOVERY_BYTES = 64 * 1024
MAX_RECOVERY_ASSET_BYTES = 16 * 1024 * 1024 * 1024

_REQUIRED_KEYS = ("AP", "AU", "AH", "AT", "CU", "CH", "CT")
_BUILD_RE = re.compile(r"\b(?P<build>\d{2}[A-Z]\d{2,6}[a-z]?)\b")


@dataclass(frozen=True)
class DiscoveryResponse:
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class RecoveryDiscoveryResult:
    state: RecoveryState
    target: RecoveryTarget
    product: Optional[RecoveryProduct]
    record_digest: str
    diagnostics: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "state": self.state.value,
            "target": self.target.to_dict(),
            "product": self.product.to_dict() if self.product else None,
            "record_digest": self.record_digest,
            "diagnostics": list(self.diagnostics),
        }


class _AppleAssetRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {APPLE_RECOVERY_HOST, "updates.cdn-apple.com", "cdn-apple.com"} or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ArtifactDownloadError("Recovery asset size probe redirect leaves the approved Apple policy")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_version(build: str) -> Optional[str]:
    prefixes = {"23": "14.0", "24": "15.0", "26": "26.0"}
    return prefixes.get(build[:2])


def _redacted_record_digest(target: RecoveryTarget, values: Mapping[str, str]) -> str:
    public = {key: value for key, value in values.items() if key not in {"AT", "CT"}}
    public["target"] = target.to_dict()  # type: ignore[assignment]
    return hashlib.sha256(json.dumps(public, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class AppleRecoveryDiscovery:
    """Use only the pinned OpenCore query protocol and Apple infrastructure."""

    def __init__(
        self,
        transport: Optional[Callable[[str, Mapping[str, str], bytes], DiscoveryResponse]] = None,
        board_id: str = SEQUOIA_BOARD_ID,
        mlb: str = ZERO_MLB,
        tool_version: str = OPENCORE_RECOVERY_TOOL_VERSION,
        discovery_host: str = APPLE_RECOVERY_HOST,
        query_scheme: str = APPLE_QUERY_SCHEME,
        asset_hosts: Tuple[str, ...] = (APPLE_RECOVERY_HOST, "updates.cdn-apple.com", "cdn-apple.com"),
        cancel: Optional[Callable[[], bool]] = None,
    ) -> None:
        if not re.fullmatch(r"Mac-[A-F0-9]{16}", board_id):
            raise ValueError("Recovery board ID is invalid")
        if not re.fullmatch(r"[A-Z0-9]{17}", mlb):
            raise ValueError("Recovery MLB must be a 17-character opaque value")
        if tool_version != OPENCORE_RECOVERY_TOOL_VERSION:
            raise ValueError("Recovery discovery must use the pinned OpenCore tool version")
        if discovery_host != APPLE_RECOVERY_HOST or query_scheme != APPLE_QUERY_SCHEME:
            raise ValueError("Recovery discovery endpoint is outside the pinned HTTPS Apple policy")
        if set(asset_hosts) != {APPLE_RECOVERY_HOST, "updates.cdn-apple.com", "cdn-apple.com"}:
            raise ValueError("Recovery asset hosts do not match the pinned Apple policy")
        self.transport = transport or self._transport
        self.board_id = board_id
        self.mlb = mlb
        self.tool_version = tool_version
        self.discovery_host = discovery_host
        self.query_scheme = query_scheme
        self.asset_hosts = tuple(asset_hosts)
        self._custom_transport = transport is not None
        self.cancel = cancel

    def _transport(self, url: str, headers: Mapping[str, str], data: bytes) -> DiscoveryResponse:
        parsed = urlparse(url)
        if parsed.scheme != self.query_scheme or parsed.hostname != self.discovery_host or parsed.port not in (None, 443):
            raise ArtifactDownloadError("Recovery discovery endpoint is outside the pinned Apple policy")
        request = Request(url=url, headers=dict(headers), data=data)
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read(MAX_DISCOVERY_BYTES + 1)
                if len(body) > MAX_DISCOVERY_BYTES:
                    raise ArtifactDownloadError("Recovery discovery response exceeds the bounded limit")
                return DiscoveryResponse({str(k): str(v) for k, v in response.headers.items()}, body)
        except ArtifactDownloadError:
            raise
        except Exception as exc:
            raise ArtifactDownloadError(f"Recovery discovery failed: {type(exc).__name__}") from exc

    def discover(self, target: RecoveryTarget) -> RecoveryDiscoveryResult:
        if target.version != "15.0" or target.build != "24A335":
            raise ValueError("This policy only discovers the frozen Sequoia 15.0/24A335 target")
        session = self._request("/")
        cookie = self._session_cookie(session.headers)
        post = {
            "cid": secrets.token_hex(8).upper(),
            "sn": self.mlb,
            "bid": self.board_id,
            "k": secrets.token_hex(32).upper(),
            "fg": secrets.token_hex(32).upper(),
            "os": "default",
        }
        response = self._request("/InstallationPayload/RecoveryImage", cookie=cookie, post=post)
        values = self._parse_key_values(response.body)
        missing = [key for key in _REQUIRED_KEYS if key not in values]
        record_digest = _redacted_record_digest(target, values)
        if missing:
            return RecoveryDiscoveryResult(RecoveryState.FAILED, target, None, record_digest, (f"missing response keys: {','.join(missing)}",))
        explicit_status = values.get("ER", "").strip().lower()
        if explicit_status in {"unavailable", "not-found", "not_found", "not available"}:
            return RecoveryDiscoveryResult(
                RecoveryState.UNAVAILABLE,
                target,
                None,
                record_digest,
                ("Apple explicitly reported the exact Recovery product as unavailable",),
            )
        build_match = _BUILD_RE.search(values["AP"])
        resolved_build = build_match.group("build") if build_match else None
        resolved_version = _build_version(resolved_build) if resolved_build else None
        if resolved_build != target.build or resolved_version != target.version:
            state = RecoveryState.UNAVAILABLE if resolved_build else RecoveryState.AMBIGUOUS
            reason = "Apple response did not identify the exact requested build" if not resolved_build else f"Apple selected build {resolved_build}, not {target.build}"
            return RecoveryDiscoveryResult(state, target, None, record_digest, (reason,))
        try:
            image_url = self._asset_url(values["AU"], self.asset_hosts)
            chunklist_url = self._asset_url(values["CU"], self.asset_hosts)
            image_size = self._asset_size(image_url, values["AT"]) if not self._custom_transport else None
            chunklist_size = self._asset_size(chunklist_url, values["CT"]) if not self._custom_transport else None
            if not self._custom_transport and (image_size is None or chunklist_size is None):
                return RecoveryDiscoveryResult(
                    RecoveryState.FAILED,
                    target,
                    None,
                    record_digest,
                    ("Apple Recovery asset sizes were not available from authenticated HTTPS metadata",),
                )
            product = RecoveryProduct(
                target=target,
                image_url=image_url,
                image_sha256=values["AH"].lower(),
                image_size_bytes=image_size,
                chunklist_url=chunklist_url,
                chunklist_sha256=values["CH"].lower(),
                chunklist_size_bytes=chunklist_size,
                image_session_ref=values["AT"],
                chunklist_session_ref=values["CT"],
            )
        except (KeyError, ValueError) as exc:
            return RecoveryDiscoveryResult(RecoveryState.FAILED, target, None, record_digest, (f"invalid Apple Recovery metadata: {type(exc).__name__}",))
        return RecoveryDiscoveryResult(RecoveryState.DISCOVERED, target, product, record_digest)

    def _asset_size(self, url: str, session_token: str) -> Optional[int]:
        request = Request(
            url=url,
            headers={"User-Agent": "MacLoader-Recovery/0.0.4", "Cookie": f"AssetToken={session_token}"},
            method="HEAD",
        )
        try:
            with build_opener(_AppleAssetRedirectHandler()).open(request, timeout=30) as response:
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in set(self.asset_hosts):
                    raise ArtifactDownloadError("Recovery asset size probe ended outside the approved Apple policy")
                raw_size = response.headers.get("Content-Length")
                if raw_size is None:
                    return None
                size = int(raw_size)
                if size <= 0 or size > MAX_RECOVERY_ASSET_BYTES:
                    return None
                return size
        except (ArtifactDownloadError, OSError, ValueError):
            return None

    def _request(self, path: str, cookie: Optional[str] = None, post: Optional[Mapping[str, str]] = None) -> DiscoveryResponse:
        if path not in {"/", "/InstallationPayload/RecoveryImage"}:
            raise ValueError("Recovery discovery path is not allowlisted")
        if self.cancel and self.cancel():
            raise ArtifactDownloadError("Recovery discovery cancelled")
        headers = {"Host": APPLE_RECOVERY_HOST, "Connection": "close", "User-Agent": "InternetRecovery/1.0"}
        if cookie:
            headers["Cookie"] = cookie
            headers["Content-Type"] = "text/plain"
        data = None if post is None else "\n".join(f"{key}={post[key]}" for key in post).encode("ascii")
        response = self.transport(f"{self.query_scheme}://{self.discovery_host}{path}", headers, data or b"")
        if self.cancel and self.cancel():
            raise ArtifactDownloadError("Recovery discovery cancelled")
        return response

    @staticmethod
    def _session_cookie(headers: Mapping[str, str]) -> str:
        for key, value in headers.items():
            if key.lower() == "set-cookie":
                for cookie in value.split("; "):
                    if cookie.startswith("session="):
                        return cookie
        raise ArtifactDownloadError("Apple Recovery discovery did not return a session cookie")

    @staticmethod
    def _parse_key_values(body: bytes) -> Dict[str, str]:
        if len(body) > MAX_DISCOVERY_BYTES:
            raise ArtifactDownloadError("Recovery discovery response exceeds the bounded limit")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactDownloadError("Recovery discovery response is not UTF-8") from exc
        values: Dict[str, str] = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            if ": " not in line:
                raise ArtifactDownloadError("Recovery discovery response contains a malformed line")
            key, value = line.split(": ", 1)
            if key in values or not re.fullmatch(r"[A-Z]{2}", key) or not value.strip():
                raise ArtifactDownloadError("Recovery discovery response contains duplicate or invalid metadata")
            values[key] = value.strip()
        return values

    @staticmethod
    def _asset_url(value: str, allowed_hosts: Tuple[str, ...] = (APPLE_RECOVERY_HOST, "updates.cdn-apple.com", "cdn-apple.com")) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in set(allowed_hosts):
            raise ValueError("Recovery asset URL is outside the approved Apple HTTPS policy")
        if parsed.username or parsed.password or parsed.port not in (None, 443) or not parsed.path:
            raise ValueError("Recovery asset URL contains unsafe URL components")
        return value
