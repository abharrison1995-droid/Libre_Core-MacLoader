"""Load only locally acquired tools whose bytes match the reviewed catalog.

The catalog is policy data, not a trust claim supplied by a caller.  A
selection is qualified only after this loader verifies every referenced byte,
the host tuple, the public source metadata, and the tool version banners.
"""

from __future__ import annotations

from dataclasses import dataclass
import platform
import os
import stat
import hashlib
import tarfile
import tempfile
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
import plistlib
import subprocess
import shutil
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse
import yaml

from macloader.config import DEFAULT_TOOLCHAIN_DIR
from macloader.dependencies.cache import compute_file_sha256
from macloader.dependencies.downloader import Downloader
from macloader.domain.dependencies import DependencyArtifact
from macloader.domain.contracts import CONTRACT_SCHEMA_VERSION, ToolchainSelection, canonical_json_digest


DEFAULT_CATALOG = Path(__file__).parent.parent / "database" / "data" / "toolchains" / "catalog.yaml"
DEFAULT_TOOL_ROOT = DEFAULT_TOOLCHAIN_DIR
MAX_TOOLCHAIN_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TOOL_BYTES = 128 * 1024 * 1024


def compute_bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    build_source_root: Optional[str] = None


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
    build_source_root: Optional[str] = None
    build = raw.get("build")
    if build is not None:
        if (
            label != "acpi_compiler"
            or not isinstance(build, dict)
            or set(build) != {"kind", "source_root"}
            or build.get("kind") != "acpica-unix-iasl"
        ):
            raise ToolchainTrustError(f"Toolchain catalog has an unsupported source build policy for {label}")
        candidate = str(build.get("source_root", ""))
        if not candidate or PurePosixPath(candidate).name != candidate or candidate in {".", ".."}:
            raise ToolchainTrustError("Toolchain catalog has an unsafe ACPICA source root")
        build_source_root = candidate
    return ToolRecord(
        name=label,
        file_name=str(_required(raw, "file_name", label)),
        version=str(_required(raw, "version", label)),
        source_url=str(_required(raw, "source_url", label)),
        source_sha256=str(_required(raw, "source_sha256", label)).lower(),
        size_bytes=int(_required(raw, "size_bytes", label)),
        sha256=str(_required(raw, "sha256", label)).lower(),
        invocation=str(_required(raw, "invocation", label)),
        build_source_root=build_source_root,
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
        self.tool_root = Path(tool_root or DEFAULT_TOOL_ROOT).expanduser().absolute()
        self.policy_version, self.records = _load_catalog(self.catalog_path)

    def _assert_safe_root(self, *, create: bool = False) -> None:
        for ancestor in (self.tool_root, *self.tool_root.parents):
            if ancestor.exists() and ancestor.is_symlink():
                raise ToolchainTrustError("Toolchain workspace contains a symlink boundary")
        if self.tool_root.exists() and not self.tool_root.is_dir():
            raise ToolchainTrustError("Toolchain root is not a directory")
        if create:
            self.tool_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.tool_root.is_symlink():
                raise ToolchainTrustError("Toolchain root must not be a symlink")

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
        self._assert_safe_root()
        if Path(record.file_name).name != record.file_name or record.file_name in {".", ".."}:
            raise ToolchainTrustError(f"Toolchain catalog contains an unsafe filename: {record.name}")
        path = self.tool_root / record.file_name
        try:
            path.resolve().relative_to(self.tool_root.resolve())
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
    def _read_zip_member(archive: Path, member_name: str, record: ToolRecord) -> bytes:
        try:
            with zipfile.ZipFile(archive) as bundle:
                matches = [item for item in bundle.infolist() if item.filename == member_name]
                if len(matches) != 1:
                    raise ToolchainTrustError(f"Pinned archive member is missing or ambiguous: {record.name}")
                info = matches[0]
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or mode == stat.S_IFLNK or info.file_size != record.size_bytes:
                    raise ToolchainTrustError(f"Pinned archive member has an unsafe type or size: {record.name}")
                if info.file_size > MAX_TOOL_BYTES:
                    raise ToolchainTrustError(f"Pinned archive member exceeds the tool size limit: {record.name}")
                data = bundle.read(info)
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            raise ToolchainTrustError(f"Unable to read pinned archive member: {record.name}") from exc
        if len(data) != record.size_bytes or compute_bytes_sha256(data) != record.sha256:
            raise ToolchainTrustError(f"Pinned archive member digest or size mismatch: {record.name}")
        return data

    @staticmethod
    def _read_tar_tool(archive: Path, record: ToolRecord) -> bytes:
        try:
            with tarfile.open(archive, "r:gz") as bundle:
                member_basename = "iasl" if record.name == "acpi_compiler" else record.file_name
                matches = [item for item in bundle.getmembers() if Path(item.name).name == member_basename]
                if len(matches) != 1:
                    raise ToolchainTrustError(f"Pinned tar tool is missing or ambiguous: {record.name}")
                info = matches[0]
                if record.name == "acpi_compiler" and info.isdir():
                    raise ToolchainTrustError(
                        "Pinned ACPICA release contains source only; no catalog-pinned Linux iASL executable is available"
                    )
                if not info.isfile() or info.size != record.size_bytes or info.size > MAX_TOOL_BYTES:
                    raise ToolchainTrustError(f"Pinned tar tool has an unsafe type or size: {record.name}")
                source = bundle.extractfile(info)
                if source is None:
                    raise ToolchainTrustError(f"Pinned tar tool cannot be read: {record.name}")
                with source:
                    data = source.read(record.size_bytes + 1)
        except (OSError, tarfile.TarError) as exc:
            raise ToolchainTrustError(f"Unable to read pinned tar tool: {record.name}") from exc
        if len(data) != record.size_bytes or compute_bytes_sha256(data) != record.sha256:
            raise ToolchainTrustError(f"Pinned tar tool digest or size mismatch: {record.name}")
        return data

    @staticmethod
    def _build_acpica_iasl(archive: Path, record: ToolRecord, staging: Path) -> bytes:
        """Build the Linux iASL from the exact ACPICA source archive.

        GCC embeds __DATE__/__TIME__ in this release. SOURCE_DATE_EPOCH and
        fixed locale/time-zone make those bytes repeatable; the final executable
        still has to match the cataloged size and SHA-256 before publication.
        """
        if not record.build_source_root:
            raise ToolchainTrustError("ACPICA iASL source build policy is missing")
        required = ("make", "cc", "gcc", "bison", "flex", "m4")
        if any(shutil.which(program) is None for program in required):
            raise ToolchainTrustError("Building pinned Linux iASL requires make, GCC, Bison, Flex and m4")
        extraction_root = staging / "acpica-source"
        extraction_root.mkdir(mode=0o700)
        max_source_files = 10_000
        max_source_bytes = 64 * 1024 * 1024
        seen: set[str] = set()
        file_count = 0
        total_bytes = 0
        try:
            with tarfile.open(archive, "r:gz") as bundle:
                members = bundle.getmembers()
                if len(members) > max_source_files:
                    raise ToolchainTrustError("Pinned ACPICA source archive contains too many entries")
                for member in members:
                    relative = PurePosixPath(member.name)
                    if (
                        relative.is_absolute()
                        or any(part in {"", ".", ".."} for part in relative.parts)
                        or not relative.parts
                        or relative.parts[0] != record.build_source_root
                    ):
                        raise ToolchainTrustError("Pinned ACPICA archive contains an unsafe source path")
                    key = relative.as_posix().casefold()
                    if key in seen:
                        raise ToolchainTrustError("Pinned ACPICA archive contains duplicate source paths")
                    seen.add(key)
                    destination = extraction_root.joinpath(*relative.parts)
                    try:
                        destination.resolve().relative_to(extraction_root.resolve())
                    except ValueError as exc:
                        raise ToolchainTrustError("Pinned ACPICA source path escapes its staging directory") from exc
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                        continue
                    if not member.isfile() or member.size < 0 or member.size > max_source_bytes:
                        raise ToolchainTrustError("Pinned ACPICA archive contains an unsafe source entry")
                    file_count += 1
                    total_bytes += member.size
                    if file_count > max_source_files or total_bytes > max_source_bytes:
                        raise ToolchainTrustError("Pinned ACPICA source exceeds extraction limits")
                    source = bundle.extractfile(member)
                    if source is None:
                        raise ToolchainTrustError("Pinned ACPICA source file cannot be read")
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with source, destination.open("xb") as target:
                        remaining = member.size
                        while remaining:
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ToolchainTrustError("Pinned ACPICA source archive is truncated")
                            target.write(chunk)
                            remaining -= len(chunk)
                    os.chmod(destination, member.mode & 0o755)
        except (OSError, tarfile.TarError) as exc:
            raise ToolchainTrustError("Unable to safely extract the pinned ACPICA source archive") from exc

        source_root = extraction_root / record.build_source_root
        makefile_dir = source_root / "generate" / "unix"
        if makefile_dir.is_symlink() or not (makefile_dir / "Makefile").is_file():
            raise ToolchainTrustError("Pinned ACPICA source is missing its reviewed Unix build entry point")
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "SOURCE_DATE_EPOCH": "0",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
        try:
            completed = subprocess.run(
                ["make", "CC=cc", "-j2", "iasl"], cwd=makefile_dir,
                env=environment, capture_output=True, text=True, timeout=300, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolchainTrustError("Pinned ACPICA source build could not run") from exc
        if completed.returncode != 0:
            raise ToolchainTrustError("Pinned ACPICA source build failed; check the local Linux compiler prerequisites")
        binary = source_root / "generate" / "unix" / "bin" / "iasl"
        if binary.is_symlink() or not binary.is_file():
            raise ToolchainTrustError("Pinned ACPICA build did not produce a regular iASL executable")
        return TrustedToolchainLoader._read_pinned_binary(binary, record)

    @staticmethod
    def _read_pinned_binary(path: Path, record: ToolRecord) -> bytes:
        try:
            if path.stat().st_size != record.size_bytes or record.size_bytes > MAX_TOOL_BYTES:
                raise ToolchainTrustError(f"Pinned tool size mismatch: {record.name}")
            with path.open("rb") as handle:
                data = handle.read(record.size_bytes + 1)
        except OSError as exc:
            raise ToolchainTrustError(f"Unable to read pinned tool: {record.name}") from exc
        if len(data) != record.size_bytes or compute_bytes_sha256(data) != record.sha256:
            raise ToolchainTrustError(f"Pinned tool digest or size mismatch: {record.name}")
        return data

    def provision(self, *, downloader: Optional[Downloader] = None) -> ToolchainSelection:
        """Acquire only the catalog-pinned archive bytes, then publish verified tools."""
        try:
            return self.select()
        except ToolchainTrustError:
            pass
        record = self.record()
        self._assert_safe_root(create=True)
        selected = (record.sample_plist, record.ocvalidate, record.acpi_compiler, record.identity_tool)
        archive_sources: dict[tuple[str, str], tuple[str, str]] = {}
        for item in selected:
            source = urlparse(item.source_url)
            key = (source.scheme, source.netloc + source.path)
            archive_sources[key] = (item.source_url.split("#", 1)[0], item.source_sha256)
        open_core_key = next((key for key in archive_sources if "OpenCore-" in key[1]), None)
        if open_core_key is None:
            raise ToolchainTrustError("Toolchain catalog does not identify the OpenCore archive")
        open_core_url, open_core_digest = archive_sources[open_core_key]
        if open_core_url != record.archive_url or open_core_digest != record.archive_sha256:
            raise ToolchainTrustError("Toolchain records disagree about the pinned OpenCore archive")
        archive_hashes = {(url, digest) for url, digest in archive_sources.values()}
        if len(archive_hashes) != len(archive_sources):
            raise ToolchainTrustError("Toolchain catalog has conflicting hashes for a shared source archive")
        fetcher = downloader or Downloader(max_download_bytes=MAX_TOOLCHAIN_ARCHIVE_BYTES)
        staging = Path(tempfile.mkdtemp(prefix="macloader-toolchain-", dir=self.tool_root.parent))
        try:
            downloaded: dict[str, Path] = {}
            for index, (url, archive_digest) in enumerate(sorted(archive_sources.values())):
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.hostname not in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"} or parsed.username or parsed.password or parsed.port not in (None, 443):
                    raise ToolchainTrustError("Toolchain archive URL is outside the pinned HTTPS GitHub release policy")
                filename = f"archive-{index}.bin"
                destination = staging / filename
                artifact = DependencyArtifact(filename, url, archive_digest, 0)
                fetcher.download_artifact(artifact, destination)
                if destination.stat().st_size > MAX_TOOLCHAIN_ARCHIVE_BYTES:
                    raise ToolchainTrustError("Toolchain archive exceeds the configured size limit")
                downloaded[url] = destination

            payloads: dict[str, tuple[bytes, int]] = {}
            for item in selected:
                source = urlparse(item.source_url)
                archive_url = item.source_url.split("#", 1)[0]
                archive_path = downloaded.get(archive_url)
                if archive_path is None:
                    raise ToolchainTrustError(f"Toolchain source archive is missing: {item.name}")
                with archive_path.open("rb") as archive_handle:
                    magic = archive_handle.read(2)
                if magic == b"PK":
                    member_name = source.fragment
                    if not member_name:
                        raise ToolchainTrustError(f"Toolchain catalog has no exact zip member for {item.name}")
                    data = self._read_zip_member(archive_path, member_name, item)
                elif magic == b"\x1f\x8b":
                    data = (
                        self._build_acpica_iasl(archive_path, item, staging)
                        if item.build_source_root
                        else self._read_tar_tool(archive_path, item)
                    )
                else:
                    if source.fragment:
                        raise ToolchainTrustError(f"Pinned standalone tool has an unexpected archive member: {item.name}")
                    data = self._read_pinned_binary(archive_path, item)
                payloads[item.file_name] = (data, 0o700 if item is not record.sample_plist else 0o600)

            staged_files: dict[str, Path] = {}
            for file_name, (data, mode) in payloads.items():
                temporary = staging / f"{file_name}.verified"
                with temporary.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, mode)
                staged_files[file_name] = temporary

            # Validate executable architecture and version while all files are
            # still quarantined in staging. A bad platform asset must not leave
            # a misleading partial toolchain in the user's workspace.
            for item in (record.ocvalidate, record.acpi_compiler, record.identity_tool):
                self._run_version(staged_files[item.file_name], item)
            try:
                schema = plistlib.loads(staged_files[record.sample_plist.file_name].read_bytes())
            except (OSError, plistlib.InvalidFileException) as exc:
                raise ToolchainTrustError("Pinned OpenCore schema is not a readable plist") from exc
            if not isinstance(schema, dict) or not {"ACPI", "Kernel", "PlatformInfo", "UEFI"}.issubset(schema):
                raise ToolchainTrustError("Pinned OpenCore schema is missing required sections")

            backups: dict[str, Path] = {}
            published: list[str] = []
            try:
                for file_name, temporary in staged_files.items():
                    destination = self.tool_root / file_name
                    if destination.is_symlink():
                        raise ToolchainTrustError(f"Refusing to replace a toolchain symlink: {file_name}")
                    if destination.exists():
                        backup = staging / f"{file_name}.previous"
                        os.replace(destination, backup)
                        backups[file_name] = backup
                    os.replace(temporary, destination)
                    published.append(file_name)
                return self.select()
            except Exception:
                for file_name in reversed(published):
                    (self.tool_root / file_name).unlink(missing_ok=True)
                for file_name, backup in backups.items():
                    os.replace(backup, self.tool_root / file_name)
                raise
        finally:
            import shutil
            shutil.rmtree(staging, ignore_errors=True)

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
        self._assert_safe_root()
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
