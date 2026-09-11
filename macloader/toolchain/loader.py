"""Load only locally acquired tools whose bytes match the reviewed catalog.

The catalog is policy data, not a trust claim supplied by a caller.  A
selection is qualified only after this loader verifies every referenced byte,
the host tuple, the public source metadata, and the tool version banners.
"""

from __future__ import annotations

from dataclasses import dataclass
import platform
from pathlib import Path
import plistlib
import subprocess
from typing import Any, Dict, Mapping, Optional
import yaml

from macloader.dependencies.cache import compute_file_sha256
from macloader.domain.contracts import CONTRACT_SCHEMA_VERSION, ToolchainSelection, canonical_json_digest


DEFAULT_CATALOG = Path(__file__).parent.parent / "database" / "data" / "toolchains" / "catalog.yaml"
DEFAULT_TOOL_ROOT = Path(__file__).resolve().parents[2] / "workspace" / "p4-toolchain"


class ToolchainTrustError(ValueError):
    """Raised when a toolchain cannot be proven to be the reviewed one."""


@dataclass(frozen=True)
class ToolRecord:
    name: str
    file_name: str
    version: str
    source_url: str
    source_sha256: str
    size_bytes: int
    sha256: str
    invocation: str


@dataclass(frozen=True)
class ToolchainRecord:
    record_id: str
    policy_version: str
    host_platform: str
    host_architecture: str
    opencore_version: str
    archive_url: str
    archive_sha256: str
    sample_plist: ToolRecord
    ocvalidate: ToolRecord
    acpi_compiler: ToolRecord
    identity_tool: ToolRecord

    @property
    def digest(self) -> str:
        return canonical_json_digest({
            "record_id": self.record_id,
            "policy_version": self.policy_version,
            "host_platform": self.host_platform,
            "host_architecture": self.host_architecture,
            "opencore_version": self.opencore_version,
            "archive_url": self.archive_url,
            "archive_sha256": self.archive_sha256,
            "sample_plist": self.sample_plist.__dict__,
            "ocvalidate": self.ocvalidate.__dict__,
            "acpi_compiler": self.acpi_compiler.__dict__,
            "identity_tool": self.identity_tool.__dict__,
        })


def _required(mapping: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise ToolchainTrustError(f"Toolchain catalog is missing {label}.{key}")
    return mapping[key]


def _tool_record(raw: Any, label: str) -> ToolRecord:
    if not isinstance(raw, dict):
        raise ToolchainTrustError(f"Toolchain catalog entry {label} must be a mapping")
    return ToolRecord(
        name=label,
        file_name=str(_required(raw, "file_name", label)),
        version=str(_required(raw, "version", label)),
        source_url=str(_required(raw, "source_url", label)),
        source_sha256=str(_required(raw, "source_sha256", label)).lower(),
        size_bytes=int(_required(raw, "size_bytes", label)),
        sha256=str(_required(raw, "sha256", label)).lower(),
        invocation=str(_required(raw, "invocation", label)),
    )


def _load_catalog(path: Path) -> tuple[str, tuple[ToolchainRecord, ...]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ToolchainTrustError(f"Unable to load trusted toolchain catalog: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != "1":
        raise ToolchainTrustError("Trusted toolchain catalog has an unsupported schema")
    policy = str(_required(data, "policy_version", "catalog"))
    raw_records = _required(data, "toolchains", "catalog")
    if not isinstance(raw_records, list) or not raw_records:
        raise ToolchainTrustError("Trusted toolchain catalog has no records")
    records = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise ToolchainTrustError("Trusted toolchain records must be mappings")
        records.append(ToolchainRecord(
            record_id=str(_required(raw, "id", "record")),
            policy_version=policy,
            host_platform=str(_required(raw, "host_platform", "record")).lower(),
            host_architecture=str(_required(raw, "host_architecture", "record")).lower(),
            opencore_version=str(_required(raw, "opencore_version", "record")),
            archive_url=str(_required(raw, "archive_url", "record")),
            archive_sha256=str(_required(raw, "archive_sha256", "record")).lower(),
            sample_plist=_tool_record(_required(raw, "sample_plist", "record"), "sample_plist"),
            ocvalidate=_tool_record(_required(raw, "ocvalidate", "record"), "ocvalidate"),
            acpi_compiler=_tool_record(_required(raw, "acpi_compiler", "record"), "acpi_compiler"),
            identity_tool=_tool_record(_required(raw, "identity_tool", "record"), "identity_tool"),
        ))
    return policy, tuple(records)


class TrustedToolchainLoader:
    """Select and verify a toolchain record for the current host."""

    def __init__(self, catalog_path: Optional[Path] = None, tool_root: Optional[Path] = None) -> None:
        self.catalog_path = catalog_path or DEFAULT_CATALOG
        self.tool_root = (tool_root or DEFAULT_TOOL_ROOT).resolve()
        self.policy_version, self.records = _load_catalog(self.catalog_path)

    @staticmethod
    def host_tuple() -> tuple[str, str]:
        host = platform.system().lower()
        arch = platform.machine().lower()
        aliases = {"amd64": "x86_64", "x86-64": "x86_64", "aarch64": "arm64"}
        return host, aliases.get(arch, arch)

    def record(self, host_platform: Optional[str] = None, host_architecture: Optional[str] = None) -> ToolchainRecord:
        host, arch = self.host_tuple()
        host = (host_platform or host).lower()
        arch = (host_architecture or arch).lower()
        matches = [item for item in self.records if item.host_platform == host and item.host_architecture == arch]
        if len(matches) != 1:
            raise ToolchainTrustError(f"No unique trusted toolchain record for host {host}/{arch}")
        return matches[0]

    def _verify_file(self, record: ToolRecord) -> Path:
        path = (self.tool_root / record.file_name).resolve()
        try:
            path.relative_to(self.tool_root)
        except ValueError as exc:
            raise ToolchainTrustError(f"Toolchain path escapes the controlled tool directory: {record.file_name}") from exc
        if path.is_symlink() or not path.is_file():
            raise ToolchainTrustError(f"Trusted tool is missing or unsafe: {record.file_name}")
        if path.stat().st_size != record.size_bytes:
            raise ToolchainTrustError(f"Trusted tool size mismatch: {record.name}")
        digest = compute_file_sha256(path)
        if digest != record.sha256:
            raise ToolchainTrustError(f"Trusted tool digest mismatch: {record.name}")
        return path

    @staticmethod
    def _run_version(path: Path, record: ToolRecord) -> str:
        try:
            completed = subprocess.run(
                [str(path), *record.invocation.split()],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolchainTrustError(f"Unable to execute trusted {record.name}: {exc}") from exc
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if record.version not in output:
            raise ToolchainTrustError(f"Trusted {record.name} did not report version {record.version}")
        return output[:4096]

    def select(self, *, require_executable_checks: bool = True) -> ToolchainSelection:
        record = self.record()
        sample = self._verify_file(record.sample_plist)
        validator = self._verify_file(record.ocvalidate)
        compiler = self._verify_file(record.acpi_compiler)
        identity = self._verify_file(record.identity_tool)
        if require_executable_checks:
            validator_banner = self._run_version(validator, record.ocvalidate)
            compiler_banner = self._run_version(compiler, record.acpi_compiler)
            identity_banner = self._run_version(identity, record.identity_tool)
        else:
            validator_banner = compiler_banner = identity_banner = "verification deferred"
        try:
            sample_data = plistlib.loads(sample.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise ToolchainTrustError(f"Trusted OpenCore schema is not a plist: {exc}") from exc
        if not isinstance(sample_data, dict) or not {"ACPI", "Kernel", "PlatformInfo", "UEFI"}.issubset(sample_data):
            raise ToolchainTrustError("Trusted OpenCore schema is missing required sections")
        provenance = {
            "source": "trusted-catalog",
            "qualification": "qualified",
            "catalog_path": self.catalog_path.name,
            "catalog_policy": self.policy_version,
            "record_id": record.record_id,
            "record_digest": record.digest,
            "archive_url": record.archive_url,
            "archive_sha256": record.archive_sha256,
            "ocvalidate_invocation": record.ocvalidate.invocation,
            "iasl_invocation": record.acpi_compiler.invocation,
            "identity_invocation": record.identity_tool.invocation,
            "ocvalidate_banner": validator_banner.replace("\n", " ")[:512],
            "iasl_banner": compiler_banner.replace("\n", " ")[:512],
            "identity_banner": identity_banner.replace("\n", " ")[:512],
        }
        return ToolchainSelection(
            schema_version=CONTRACT_SCHEMA_VERSION,
            opencore_version=record.opencore_version,
            ocvalidate_version=record.ocvalidate.version,
            acpi_compiler=record.acpi_compiler.version,
            identity_tool=record.identity_tool.version,
            recovery_tool=None,
            host_platform=record.host_platform,
            host_architecture=record.host_architecture,
            provenance=provenance,
            ocvalidate_path=str(validator),
            ocvalidate_sha256=record.ocvalidate.sha256,
            acpi_compiler_path=str(compiler),
            acpi_compiler_sha256=record.acpi_compiler.sha256,
            identity_tool_path=str(identity),
            identity_tool_sha256=record.identity_tool.sha256,
            sample_plist_path=str(sample),
            sample_plist_sha256=record.sample_plist.sha256,
        )

    def verify_selection(self, selection: ToolchainSelection) -> ToolchainSelection:
        """Reject forged selections by recomputing the catalog-derived choice."""
        trusted = self.select()
        if selection.to_dict() != trusted.to_dict():
            raise ToolchainTrustError("Caller-supplied toolchain selection does not match the trusted catalog")
        return trusted
