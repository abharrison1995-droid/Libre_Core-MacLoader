"""Build a guarded EFI tree from an actionable plan and verified artifacts."""

from dataclasses import dataclass, replace
import hashlib
import json
import getpass
import plistlib
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from macloader.dependencies.archive import (
    DEFAULT_MAX_ARTIFACT_EXPANDED_BYTES,
    DEFAULT_MAX_BUILD_EXPANDED_BYTES,
    safe_extract_zip,
    validate_zip_archive,
)
from macloader.dependencies.cache import compute_file_sha256
from macloader.dependencies.resolver import DependencyResolver
from macloader.database.loader import Database, get_database
from macloader.domain.build_plan import BuildPlan
from macloader.domain.contracts import (
    BuildManifest,
    CONTRACT_SCHEMA_VERSION,
    IdentityReference,
    ToolchainSelection,
    ValidationReport,
    canonical_json_digest,
)
from macloader.domain.dependencies import ResolvedDependency, ResolvedDependencySet
from macloader.exceptions import ArchiveSecurityError, BuildPlanError
from macloader.build.acpi import AcpiProcessor
from macloader.build.config import ReviewedEfiProfile, SchemaDrivenConfigGenerator, effective_profile_digest
from macloader.config import DEFAULT_IDENTITY_DIR, DEFAULT_WORKSPACE_DIR
from macloader.identity.service import IdentityService, IdentityServiceError
from macloader.toolchain.loader import TrustedToolchainLoader, ToolchainTrustError


@dataclass
class EfiBuildResult:
    output_dir: Path
    manifest: BuildManifest
    validation: ValidationReport
    identity: IdentityReference


class EfiBuilder:
    """Construct only from an actionable, complete dependency lock."""

    def __init__(
        self,
        db: Optional[Database] = None,
        identity_store_dir: Optional[Path] = None,
        max_artifact_expanded_bytes: int = DEFAULT_MAX_ARTIFACT_EXPANDED_BYTES,
        max_build_expanded_bytes: int = DEFAULT_MAX_BUILD_EXPANDED_BYTES,
    ) -> None:
        self.db = db or get_database()
        self.identity_store_dir = identity_store_dir or DEFAULT_IDENTITY_DIR
        self.max_artifact_expanded_bytes = max_artifact_expanded_bytes
        self.max_build_expanded_bytes = max_build_expanded_bytes

    def build(
        self,
        plan: BuildPlan,
        dependencies: ResolvedDependencySet,
        artifact_paths: Dict[str, Path],
        output_dir: Path,
        fake_identity: Optional[Dict[str, str]] = None,
        toolchain: Optional[ToolchainSelection] = None,
        reviewed_profile: Optional[ReviewedEfiProfile] = None,
        private_acpi_capture: Optional[Path] = None,
        expected_acpi_evidence_digest: Optional[str] = None,
        cancel: Optional[Callable[[], bool]] = None,
        identity_reference: Optional[IdentityReference] = None,
        synthetic_test_mode: bool = False,
    ) -> EfiBuildResult:
        profile_bound = bool(
            plan.profile_bindings
            or plan.accepted_configuration_digest
            or plan.effective_profile_digest
            or plan.effective_option_selections
        )
        if profile_bound and reviewed_profile is None:
            raise BuildPlanError(
                "Machine-bound BuildPlan requires the reviewed EFI profile and private evidence; "
                "legacy configuration generation is not permitted"
            )
        if not plan.support_state.is_usable or plan.unresolved_requirements:
            reasons = "; ".join(plan.unresolved_requirements) or "hardware support state is not actionable"
            raise BuildPlanError(f"EFI build is blocked until the plan is actionable and build-ready: {reasons}")
        if toolchain is None:
            raise BuildPlanError("EFI build requires a validated toolchain selection")
        if not profile_bound and not synthetic_test_mode:
            raise BuildPlanError(
                "Unprofiled EFI builds are disabled outside explicit synthetic test mode"
            )
        if not synthetic_test_mode or reviewed_profile is not None:
            try:
                TrustedToolchainLoader().verify_selection(toolchain)
            except ToolchainTrustError as exc:
                raise BuildPlanError(f"P4 EFI generation requires the trusted toolchain selection: {exc}") from exc
            if toolchain.provenance.get("source") != "trusted-catalog":
                raise BuildPlanError("P4 EFI generation requires a toolchain selected by the trusted catalog")
        if reviewed_profile is not None:
            if private_acpi_capture is None:
                raise BuildPlanError("P4 EFI generation requires the private machine-bound ACPI capture")
            if not expected_acpi_evidence_digest:
                raise BuildPlanError("P4 EFI generation requires the accepted ACPI evidence digest")
            if not plan.evidence_digests:
                raise BuildPlanError("Machine-bound BuildPlan has no accepted evidence digests")
            if expected_acpi_evidence_digest.lower() not in {
                item.lower() for item in plan.evidence_digests
            }:
                raise BuildPlanError("Accepted ACPI evidence digest is not bound to the BuildPlan")
            if plan.stable_model_id and reviewed_profile.model_id != plan.stable_model_id:
                raise BuildPlanError("Reviewed EFI profile model does not match the accepted BuildPlan")
            if (
                reviewed_profile.product_id != plan.target_macos
                or reviewed_profile.version != plan.target_version
                or reviewed_profile.build != plan.target_build
            ):
                raise BuildPlanError("Reviewed EFI profile target does not match the accepted BuildPlan")
            if plan.profile_bindings and reviewed_profile.profile_id not in plan.profile_bindings:
                raise BuildPlanError("Reviewed EFI profile is not bound to the accepted BuildPlan")
            expected_effective_profile_digest = effective_profile_digest(
                reviewed_profile, dict(plan.effective_option_selections)
            )
            if plan.effective_profile_digest.lower() != expected_effective_profile_digest.lower():
                raise BuildPlanError("Reviewed EFI profile digest does not match the effective BuildPlan profile")
        if not dependencies.is_complete or dependencies.unresolved_requirements:
            reasons = "; ".join(dependencies.unresolved_requirements) or "dependency set is incomplete"
            raise BuildPlanError(f"EFI build is blocked by unresolved dependency or policy requirements: {reasons}")
        if not dependencies.catalog_digest or not dependencies.policy_version:
            raise BuildPlanError("EFI build requires a complete catalog lock with policy identity")
        if dependencies.plan_digest != plan.canonical_digest():
            raise BuildPlanError("EFI build dependency lock is bound to a different BuildPlan")
        if plan.target_model != dependencies.target_model or plan.target_macos.lower() != dependencies.target_macos.lower():
            raise BuildPlanError("EFI build plan and dependency lock target do not match")
        catalog = self.db.get_dependency_catalog()
        if not catalog or dependencies.policy_version != catalog.policy_version:
            raise BuildPlanError("EFI build dependency policy is stale")
        opencore_spec = self.db.get_dependency_spec("opencore")
        if opencore_spec is None or toolchain.opencore_version != opencore_spec.version or toolchain.ocvalidate_version != opencore_spec.version:
            raise BuildPlanError("EFI build toolchain is not bound to the catalog OpenCore and ocvalidate version")
        if not toolchain.host_platform or not toolchain.host_architecture:
            raise BuildPlanError("EFI build toolchain is missing host identity")
        if dependencies.catalog_digest != DependencyResolver(db=self.db).catalog_digest():
            raise BuildPlanError("EFI build dependency catalog digest is stale")
        if not plan.policy_version or plan.policy_version != catalog.policy_version:
            raise BuildPlanError("EFI build plan policy is stale or missing")
        expected_resolution = DependencyResolver(db=self.db).resolve(plan, dependencies.variant)
        if not expected_resolution.is_complete or expected_resolution.unresolved_requirements:
            raise BuildPlanError("EFI build plan cannot be resolved to a complete dependency lock")

        def signature(item: ResolvedDependency) -> tuple[Any, ...]:
            return (
                item.dependency_id.lower(), item.project_name, item.version, item.variant.value,
                tuple(item.subcomponents), item.artifact.to_dict(), item.reason, item.required_by, item.is_transitive,
            )

        if [signature(item) for item in dependencies.resolved_dependencies] != [signature(item) for item in expected_resolution.resolved_dependencies]:
            raise BuildPlanError("EFI build dependency lock does not exactly match the current BuildPlan resolution")
        dependency_ids = [item.dependency_id.lower() for item in dependencies.resolved_dependencies]
        if len(dependency_ids) != len(set(dependency_ids)):
            raise BuildPlanError("EFI build dependency lock contains duplicate dependency IDs")
        for dependency in dependencies.resolved_dependencies:
            spec = self.db.get_dependency_spec(dependency.dependency_id)
            current_artifact = spec.get_artifact(dependency.variant) if spec else None
            if spec is None or current_artifact is None:
                raise BuildPlanError(f"EFI build dependency is absent from the current catalog: {dependency.dependency_id}")
            if (
                dependency.project_name != spec.project_name
                or dependency.version != spec.version
                or list(dependency.subcomponents) != list(spec.subcomponents)
                or dependency.artifact.to_dict() != current_artifact.to_dict()
            ):
                raise BuildPlanError(f"EFI build dependency lock entry is stale or modified: {dependency.dependency_id}")
            if dependency.variant != dependencies.variant or dependency.artifact.variant != dependencies.variant:
                raise BuildPlanError(f"EFI build dependency variant is inconsistent: {dependency.dependency_id}")
        if set(artifact_paths) != set(dependency_ids):
            raise BuildPlanError("EFI build artifact paths must exactly match the dependency lock")
        if output_dir.exists():
            raise BuildPlanError(f"Refusing to overwrite an existing EFI output directory: {output_dir}")

        parent = output_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="macloader-efi-", dir=parent))
        identity_preexisting = True
        try:
            if cancel and cancel():
                raise BuildPlanError("EFI build cancelled")
            efi_root = staging / "EFI"
            for relative in ("BOOT", "OC/ACPI", "OC/Drivers", "OC/Kexts", "OC/Tools"):
                (efi_root / relative).mkdir(parents=True, exist_ok=True)
            licenses = staging / "LICENSES"
            licenses.mkdir()

            kexts: List[str] = []
            drivers: List[str] = []
            license_digests: Dict[str, str] = {}
            total_build_bytes = 0
            acpi_result = None

            def _track_build_bytes(chunk_len: int) -> None:
                nonlocal total_build_bytes
                total_build_bytes += chunk_len
                if total_build_bytes > self.max_build_expanded_bytes:
                    raise BuildPlanError(
                        f"EFI build exceeded maximum peak disk budget ({self.max_build_expanded_bytes} bytes)"
                    )

            for dependency in dependencies.resolved_dependencies:
                if cancel and cancel():
                    raise BuildPlanError("EFI build cancelled")
                archive_path = artifact_paths.get(dependency.dependency_id)
                if archive_path is None or not archive_path.is_file() or archive_path.is_symlink():
                    raise BuildPlanError(f"Verified archive is missing for {dependency.dependency_id}")
                spec_components = list(dependency.subcomponents)
                self._extract_selected(
                    archive_path,
                    spec_components,
                    efi_root,
                    dependency.dependency_id,
                    dependency.artifact.sha256.lower(),
                    dependency.artifact.size_bytes,
                    staging_root=staging,
                    cumulative_bytes_tracker=_track_build_bytes,
                    cancel=cancel,
                )
                for component in spec_components:
                    normalized_component = component.replace("\\", "/")
                    if normalized_component.endswith(".kext"):
                        kext_rel = normalized_component.removeprefix("Kexts/")
                        kexts.append(kext_rel)
                    elif normalized_component.startswith("EFI/OC/Drivers/") and Path(normalized_component).name.endswith(".efi"):
                        drivers.append(Path(normalized_component).name)
                spec = self.db.get_dependency_spec(dependency.dependency_id)
                if spec is None or not spec.license_file or not spec.license_sha256:
                    raise BuildPlanError(f"Pinned license metadata is missing for {dependency.dependency_id}")
                license_path = self.db.data_dir / spec.license_file
                data_root = self.db.data_dir.resolve()
                try:
                    license_path.resolve().relative_to(data_root)
                except ValueError as exc:
                    raise BuildPlanError(f"Pinned license path escapes the database for {dependency.dependency_id}") from exc
                if not license_path.is_file() or license_path.is_symlink():
                    raise BuildPlanError(
                        f"Pinned license notice is missing for {dependency.dependency_id}"
                    )
                try:
                    license_text = license_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise BuildPlanError(
                        f"Pinned license notice could not be read for {dependency.dependency_id}: {exc}"
                    ) from exc
                if not license_text.strip():
                    raise BuildPlanError(f"Pinned license notice is empty for {dependency.dependency_id}")
                license_digest = compute_file_sha256(license_path)
                if license_digest != spec.license_sha256.lower():
                    raise BuildPlanError(f"Pinned license notice integrity mismatch for {dependency.dependency_id}")
                license_digests[dependency.dependency_id] = license_digest
                (licenses / f"{dependency.dependency_id}.txt").write_text(license_text, encoding="utf-8")

            if reviewed_profile is not None:
                if cancel and cancel():
                    raise BuildPlanError("EFI build cancelled")
                if toolchain.acpi_compiler_path is None or toolchain.acpi_compiler_sha256 is None:
                    raise BuildPlanError("P4 EFI generation requires the trusted iasl path and digest")
                if private_acpi_capture is None:
                    raise BuildPlanError("P4 EFI generation requires the private machine-bound ACPI capture")
                acpi_result = AcpiProcessor(
                    Path(toolchain.acpi_compiler_path), toolchain.acpi_compiler_sha256,
                    work_root=DEFAULT_WORKSPACE_DIR / "p4-acpi-build",
                ).build(
                    private_acpi_capture, efi_root / "OC" / "ACPI",
                    expected_bios_binding=reviewed_profile.bios_binding,
                    expected_snapshot_id=plan.hardware_snapshot_id,
                    expected_evidence_digest=expected_acpi_evidence_digest,
                    cancel=cancel,
                )

            identity_path = self._identity_path(plan, dependencies, identity_reference)
            stored_identity = self._load_identity(identity_path) if identity_path.is_file() else None
            if fake_identity is not None and stored_identity is not None and fake_identity != stored_identity:
                raise BuildPlanError("Explicit EFI identity conflicts with the stored identity for this build scope")
            identity_preexisting = stored_identity is not None
            identity_data = stored_identity or fake_identity
            if identity_data is None:
                raise BuildPlanError("EFI build requires an explicit private identity or a deliberate stored identity")
            if reviewed_profile is not None:
                try:
                    IdentityService.validate(identity_data)
                except IdentityServiceError as exc:
                    raise BuildPlanError(f"P4 EFI identity failed the private lifecycle policy: {exc}") from exc
            identity_errors = self._identity_errors(identity_data)
            if identity_errors:
                raise BuildPlanError("Invalid EFI identity: " + "; ".join(identity_errors))
            if reviewed_profile is None:
                if synthetic_test_mode and toolchain.sample_plist_path and toolchain.sample_plist_sha256:
                    generator = SchemaDrivenConfigGenerator(
                        Path(toolchain.sample_plist_path), toolchain.sample_plist_sha256
                    )
                    config = generator.generate_synthetic_for_test(
                        identity=identity_data, kexts=kexts, drivers=drivers
                    )
                    generator.write(config, efi_root / "OC" / "config.plist")
                    schema_digest = generator.schema_digest
                else:
                    self._write_config(efi_root / "OC" / "config.plist", kexts, drivers, identity_data, toolchain.opencore_version)
                    schema_digest = ""
                profile_digest = ""
                acpi_digest = ""
                evidence_digests: tuple[str, ...] = ()
                usb_policy_state = ""
                usb_first_install_route = ""
            else:
                if toolchain.sample_plist_path is None or toolchain.sample_plist_sha256 is None:
                    raise BuildPlanError("P4 EFI generation requires the trusted OpenCore schema path and digest")
                generator = SchemaDrivenConfigGenerator(Path(toolchain.sample_plist_path), toolchain.sample_plist_sha256)
                acpi_files = acpi_result.output_files if acpi_result is not None else ()
                config = generator.generate(
                    reviewed_profile,
                    identity=identity_data,
                    kexts=kexts,
                    drivers=drivers,
                    acpi_files=acpi_files,
                    opencore_version=toolchain.opencore_version,
                    acpi_digest=acpi_result.generated_digest if acpi_result is not None else "",
                    evidence_digests=((acpi_result.source_evidence_digest,) if acpi_result is not None else ()),
                    effective_options=dict(plan.effective_option_selections),
                )
                generator.write(config, efi_root / "OC" / "config.plist")
                schema_digest = generator.schema_digest
                profile_digest = expected_effective_profile_digest
                acpi_digest = acpi_result.generated_digest if acpi_result is not None else ""
                evidence_digests = (acpi_result.source_evidence_digest,) if acpi_result is not None else ()
                usb_policy_state = reviewed_profile.usb.usb_c_correlation
                usb_first_install_route = reviewed_profile.usb.first_install_route
            validation = self.validate_tree(staging, toolchain=None, identity=identity_data, cancel=cancel)
            if validation.errors:
                raise BuildPlanError("Generated EFI failed structural validation: " + "; ".join(validation.errors))

            output_digest = self._tree_digest(staging, cancel=cancel)
            manifest = BuildManifest(
                schema_version=CONTRACT_SCHEMA_VERSION,
                build_digest=canonical_json_digest({
                    "plan_digest": plan.canonical_digest(),
                    "dependency_digest": dependencies.canonical_digest(),
                    "toolchain_digest": toolchain.digest,
                    "identity_digest": canonical_json_digest(identity_data),
                    "output_digest": output_digest,
                    "schema_digest": schema_digest,
                    "profile_digest": profile_digest,
                    "acpi_digest": acpi_digest,
                    "evidence_digests": list(evidence_digests),
                    "usb_policy_state": usb_policy_state,
                    "usb_first_install_route": usb_first_install_route,
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                }),
                target_model=plan.target_model,
                target_macos=plan.target_macos,
                artifact_lock_digest=dependencies.to_artifact_lock().digest,
                validation_report="VALID",
                toolchain_digest=toolchain.digest,
                identity_digest=canonical_json_digest(identity_data),
                output_digest=output_digest,
                license_digests=license_digests,
                schema_digest=schema_digest,
                profile_digest=profile_digest,
                acpi_digest=acpi_digest,
                evidence_digests=evidence_digests,
                usb_policy_state=usb_policy_state,
                usb_first_install_route=usb_first_install_route,
                identity_reference=identity_path.name,
                output_paths={"efi": "EFI", "licenses": "LICENSES"},
            )
            (staging / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
            validation = self.validate_tree(
                staging,
                toolchain=toolchain,
                identity=identity_data,
                expected_manifest=manifest,
                cancel=cancel,
                synthetic_test_mode=synthetic_test_mode,
            )
            if validation.status != "VALID":
                raise BuildPlanError("Published EFI failed manifest validation: " + "; ".join(validation.errors))
            if not identity_preexisting:
                if reviewed_profile is not None:
                    try:
                        IdentityService(identity_path.parent).store(identity_data, storage_ref=identity_path.name)
                    except IdentityServiceError as exc:
                        raise BuildPlanError(f"Unable to store private EFI identity: {exc}") from exc
                else:
                    self._write_private_identity(identity_path, identity_data)
            if cancel and cancel():
                raise BuildPlanError("EFI build cancelled before publication")
            staging.replace(output_dir)
            identity_ref = IdentityReference(CONTRACT_SCHEMA_VERSION, identity_path.name, redacted=True)
            return EfiBuildResult(output_dir, manifest, validation, identity_ref)
        except BaseException:
            if "identity_path" in locals() and not identity_preexisting:
                try:
                    identity_path.unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def validate_tree(
        self,
        root: Path,
        toolchain: Optional[ToolchainSelection] = None,
        timeout_seconds: float = 30.0,
        identity: Optional[Dict[str, str]] = None,
        expected_manifest: Optional[BuildManifest] = None,
        cancel: Optional[Callable[[], bool]] = None,
        synthetic_test_mode: bool = False,
    ) -> ValidationReport:
        required = [
            root / "EFI" / "BOOT" / "BOOTx64.efi",
            root / "EFI" / "OC" / "OpenCore.efi",
            root / "EFI" / "OC" / "Drivers" / "OpenRuntime.efi",
            root / "EFI" / "OC" / "config.plist",
        ]
        errors = [f"Missing required output: {path.relative_to(root)}" for path in required if not path.is_file() or path.is_symlink()]
        checks: Dict[str, str] = {"structure": "PASS" if not errors else "FAIL"}
        warnings: List[str] = []
        identity_for_redaction = identity
        try:
            if (root / "EFI" / "OC" / "config.plist").is_file():
                with (root / "EFI" / "OC" / "config.plist").open("rb") as handle:
                    config = plistlib.load(handle)
                if not isinstance(config, dict):
                    errors.append("config.plist root must be a dictionary")
                else:
                    errors.extend(self._config_file_errors(root, config))
                    generic = config.get("PlatformInfo", {}).get("Generic") if isinstance(config.get("PlatformInfo"), dict) else None
                    identity_for_redaction = identity
                    if identity_for_redaction is None and isinstance(generic, dict):
                        identity_for_redaction = generic
                    if not isinstance(generic, dict) or self._identity_errors(generic):
                        errors.append("PlatformInfo.Generic does not contain a valid identity")
                    elif identity is not None:
                        for identity_key, identity_value in identity.items():
                            expected_value: Any = bytes.fromhex(identity_value) if identity_key == "ROM" else identity_value
                            if generic.get(identity_key) != expected_value:
                                errors.append("PlatformInfo.Generic does not match the selected private identity")
                                break
                    if toolchain is not None:
                        configured_version = config.get("OC", {}).get("Version") if isinstance(config.get("OC"), dict) else None
                        if configured_version is not None and configured_version != toolchain.opencore_version:
                            errors.append("config.plist OpenCore version does not match the selected toolchain")
        except (OSError, plistlib.InvalidFileException) as exc:
            errors.append(f"Invalid config.plist: {exc}")
        if errors:
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, errors, warnings)
        try:
            output_digest = self._tree_digest(root, cancel=cancel)
        except BuildPlanError as exc:
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, [str(exc)], warnings)
        checks["output_digest"] = output_digest
        manifest_path = root / "manifest.json"
        release_bound = expected_manifest is not None and bool(
            expected_manifest.profile_digest
            or expected_manifest.acpi_digest
            or expected_manifest.evidence_digests
        )
        if toolchain is not None and not synthetic_test_mode:
            try:
                TrustedToolchainLoader().verify_selection(toolchain)
            except ToolchainTrustError as exc:
                return ValidationReport(
                    CONTRACT_SCHEMA_VERSION,
                    "INVALID",
                    toolchain.ocvalidate_version,
                    checks,
                    [f"Toolchain is not proven by the trusted catalog: {exc}"],
                    warnings,
                )
            if toolchain.provenance.get("source") != "trusted-catalog":
                return ValidationReport(
                    CONTRACT_SCHEMA_VERSION,
                    "INVALID",
                    toolchain.ocvalidate_version,
                    checks,
                    ["Toolchain is not selected by the trusted catalog"],
                    warnings,
                )
        if expected_manifest is not None and (not manifest_path.is_file() or manifest_path.is_symlink()):
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Expected build manifest is missing or unsafe"], warnings)
        if (
            toolchain is not None
            and toolchain.provenance.get("qualification") == "qualified"
            and expected_manifest is None
        ):
            return ValidationReport(
                CONTRACT_SCHEMA_VERSION,
                "INVALID",
                toolchain.ocvalidate_version,
                checks,
                ["Qualified release validation requires an expected manifest bound to the build inputs"],
                warnings,
            )
        if manifest_path.is_file() and not manifest_path.is_symlink():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                required_manifest_keys = {
                    "schema_version", "build_digest", "target_model", "target_macos",
                    "artifact_lock_digest", "validation_report", "toolchain_digest",
                    "identity_digest", "output_digest", "license_digests", "output_paths",
                    "schema_digest", "profile_digest", "acpi_digest", "evidence_digests",
                    "usb_policy_state", "usb_first_install_route", "identity_reference",
                }
                if set(manifest_data) != required_manifest_keys:
                    return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Build manifest has an invalid schema"], warnings)
                digest_fields = ("build_digest", "artifact_lock_digest", "toolchain_digest", "identity_digest", "output_digest")
                if any(not isinstance(manifest_data.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_data[field]) for field in digest_fields):
                    return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Build manifest contains invalid digest fields"], warnings)
                if manifest_data.get("output_digest") != output_digest:
                    return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Published output changed after validation"], warnings)
                if manifest_data.get("validation_report") != "VALID":
                    return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Build manifest is not marked VALID"], warnings)
                license_digests = manifest_data.get("license_digests")
                if (
                    not isinstance(license_digests, dict)
                    or any(
                        not isinstance(key, str)
                        or not isinstance(value, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", value)
                        for key, value in license_digests.items()
                    )
                ):
                    return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Build manifest contains invalid license digests"], warnings)
                if expected_manifest is not None and manifest_data != expected_manifest.to_dict():
                    return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Build manifest identity does not match the validated inputs"], warnings)
            except (OSError, ValueError) as exc:
                return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, [f"Invalid build manifest: {exc}"], warnings)
        if toolchain is None or not toolchain.ocvalidate_path:
            warnings.append("Matching ocvalidate was not supplied; structural validation is not release validation")
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "STRUCTURAL_ONLY", None, checks, [], warnings)
        validator = Path(toolchain.ocvalidate_path)
        if not validator.is_file() or validator.is_symlink():
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version, checks, ["Matching ocvalidate executable is missing or unsafe"], warnings)
        if not toolchain.ocvalidate_sha256 or compute_file_sha256(validator) != toolchain.ocvalidate_sha256.lower():
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version, checks, ["ocvalidate integrity does not match the selected toolchain"], warnings)
        if toolchain.provenance.get("qualification") != "qualified":
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version, checks, ["Selected toolchain is not qualified for release validation"], warnings)
        command = [str(validator), str(root / "EFI" / "OC" / "config.plist")]
        if validator.suffix.lower() == ".py":
            command = [sys.executable] + command
        try:
            process: Optional[subprocess.Popen[str]] = None
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=os.name != "nt",
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
                        else 0
                    ),
                )
                deadline = time.monotonic() + timeout_seconds
                while True:
                    if cancel and cancel():
                        self._terminate_process(process)
                        return ValidationReport(
                            CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version,
                            checks, ["EFI validation cancelled"], warnings,
                        )
                    try:
                        stdout, stderr = process.communicate(timeout=0.1)
                        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            self._terminate_process(process)
                            stdout, stderr = process.communicate()
                            return ValidationReport(
                                CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version,
                                checks, ["ocvalidate timed out"], warnings,
                            )
            except BaseException:
                self._terminate_process(process)
                raise
        except subprocess.TimeoutExpired:
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version, checks, ["ocvalidate timed out"], warnings)
        except OSError as exc:
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version, checks, [f"ocvalidate could not be executed: {exc}"], warnings)
        checks["ocvalidate_exit"] = str(completed.returncode)
        diagnostics = self._redact_diagnostics("\n".join(item for item in (completed.stdout.strip(), completed.stderr.strip()) if item), identity_for_redaction)[:4096]
        if completed.returncode != 0:
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", toolchain.ocvalidate_version, checks, [diagnostics or f"ocvalidate exited with status {completed.returncode}"], warnings)
        return ValidationReport(CONTRACT_SCHEMA_VERSION, "VALID", toolchain.ocvalidate_version, checks, [], warnings)

    @staticmethod
    def _terminate_process(process: Optional[subprocess.Popen[str]]) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            pid = getattr(process, "pid", None)
            if os.name != "nt" and isinstance(pid, int) and pid > 0:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                pid = getattr(process, "pid", None)
                if os.name == "nt" and isinstance(pid, int) and pid > 0:
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(pid)],
                        capture_output=True, check=False,
                    )
                elif os.name != "nt" and isinstance(pid, int) and pid > 0:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass

    @staticmethod
    def _tree_digest(root: Path, cancel: Optional[Callable[[], bool]] = None) -> str:
        records: List[Dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if cancel and cancel():
                raise BuildPlanError("EFI validation cancelled")
            if path.is_symlink():
                raise BuildPlanError(f"EFI output contains an unsafe symlink: {path.relative_to(root)}")
            if not path.is_file() or path.name == "manifest.json":
                continue
            if cancel is None:
                digest = compute_file_sha256(path)
            else:
                hasher = hashlib.sha256()
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        if cancel():
                            raise BuildPlanError("EFI validation cancelled")
                        hasher.update(chunk)
                digest = hasher.hexdigest()
            records.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": digest})
        return canonical_json_digest({"files": records})

    @staticmethod
    def _config_file_errors(root: Path, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        uefi = config.get("UEFI")
        kernel = config.get("Kernel")
        if not isinstance(uefi, dict):
            errors.append("UEFI configuration must be a dictionary")
        if not isinstance(kernel, dict):
            errors.append("Kernel configuration must be a dictionary")
        drivers = uefi.get("Drivers", []) if isinstance(uefi, dict) else []
        kexts = kernel.get("Add", []) if isinstance(kernel, dict) else []
        for label, entries, base in (("driver", drivers, root / "EFI" / "OC" / "Drivers"), ("kext", kexts, root / "EFI" / "OC" / "Kexts")):
            seen: set[str] = set()
            if not isinstance(entries, list):
                errors.append(f"{label} configuration must be a list")
                continue
            for entry in entries:
                key = "Path" if label == "driver" else "BundlePath"
                if not isinstance(entry, dict) or not isinstance(entry.get(key), str):
                    errors.append(f"Malformed {label} configuration entry")
                    continue
                name = entry[key]
                if Path(name).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", name) or name.startswith(("\\\\", "/")):
                    errors.append(f"Configured {label} must use a relative component path: {name}")
                    continue
                case_key = name.casefold()
                if case_key in seen:
                    errors.append(f"Duplicate {label} configuration entry: {name}")
                seen.add(case_key)
                raw_candidate = base / name
                if raw_candidate.is_symlink():
                    errors.append(f"Configured {label} is a symlink: {name}")
                    continue
                candidate = raw_candidate.resolve()
                try:
                    candidate.relative_to(base.resolve())
                except ValueError:
                    errors.append(f"Configured {label} escapes its component directory: {name}")
                    continue
                valid_kind = candidate.is_file() if label == "driver" else candidate.is_dir()
                if not valid_kind:
                    errors.append(f"Configured {label} is missing: {name}")
                elif any(item.is_symlink() for item in candidate.rglob("*")):
                    errors.append(f"Configured {label} contains a symlink: {name}")
        return errors

    def _extract_selected(
        self,
        archive_path: Path,
        components: Iterable[str],
        efi_root: Path,
        dependency_id: str,
        expected_sha256: str,
        expected_size: int,
        staging_root: Optional[Path] = None,
        cumulative_bytes_tracker: Optional[Callable[[int], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> int:
        root_staging = staging_root or efi_root.parent
        with tempfile.TemporaryDirectory(prefix=f"macloader-{dependency_id}-") as temp_name:
            temp = Path(temp_name)
            snapshot = temp / "archive.zip"
            try:
                shutil.copyfile(archive_path, snapshot, follow_symlinks=False)
            except OSError as exc:
                raise BuildPlanError(f"Unable to snapshot archive for {dependency_id}: {exc}") from exc

            if snapshot.stat().st_size != expected_size or compute_file_sha256(snapshot) != expected_sha256:
                raise BuildPlanError(f"Verified archive changed or does not match the lock for {dependency_id}")

            try:
                names = validate_zip_archive(snapshot, max_expanded_bytes=self.max_artifact_expanded_bytes)
            except ArchiveSecurityError as exc:
                raise BuildPlanError(f"Archive security validation failed for {dependency_id}: {exc}") from exc

            member_map: Dict[str, Path] = {}
            for component in components:
                normalized_comp = component.replace("\\", "/")
                candidates = [name for name in names if name.rstrip("/") == normalized_comp or name.rstrip("/").endswith("/" + normalized_comp)]
                if not candidates:
                    nested = [name for name in names if ("/" + normalized_comp + "/") in name or name.startswith(normalized_comp + "/")]
                    if nested:
                        nested_candidates = set()
                        for nested_name in nested:
                            if ("/" + normalized_comp + "/") in nested_name:
                                prefix = nested_name.split("/" + normalized_comp + "/", 1)[0] + "/"
                                nested_candidates.add(prefix + normalized_comp + "/")
                            else:
                                nested_candidates.add(normalized_comp + "/")
                        candidates = sorted(nested_candidates)
                if not candidates:
                    raise BuildPlanError(f"Component {component} is missing from {archive_path.name}")
                preferred = [
                    candidate
                    for candidate in candidates
                    if candidate.startswith("X64/") or "/X64/" in candidate
                ]
                if preferred:
                    candidates = preferred
                if len(candidates) != 1:
                    raise BuildPlanError(
                        f"Component {component} has ambiguous archive members in {archive_path.name}: {sorted(candidates)}"
                    )
                candidate = candidates[0].rstrip("/")
                prefix = candidate[: -len(normalized_comp)] if candidate.endswith(normalized_comp) else ""

                if normalized_comp.startswith("EFI/"):
                    base_dest = efi_root.parent / normalized_comp
                else:
                    kext_rel = normalized_comp.removeprefix("Kexts/")
                    base_dest = efi_root / "OC" / "Kexts" / kext_rel

                is_dir = (
                    candidate.endswith(".kext")
                    or any(name.startswith(candidate + "/") for name in names)
                    or (candidate + "/") in names
                )
                if is_dir:
                    for name in names:
                        if name == candidate or name == candidate + "/" or name.startswith(candidate + "/"):
                            rel_within = name[len(candidate):].lstrip("/")
                            target = base_dest / rel_within if rel_within else base_dest
                            if name in member_map and member_map[name] != target:
                                raise BuildPlanError(f"Ambiguous destination for archive member: {name}")
                            member_map[name] = target
                else:
                    if candidate in member_map and member_map[candidate] != base_dest:
                        raise BuildPlanError(f"Ambiguous destination for archive member: {candidate}")
                    member_map[candidate] = base_dest

            try:
                bytes_extracted = safe_extract_zip(
                    snapshot,
                    target_dir=root_staging,
                    member_map=member_map,
                    is_trusted_snapshot=True,
                    max_expanded_bytes=self.max_artifact_expanded_bytes,
                    cumulative_bytes_tracker=cumulative_bytes_tracker,
                    cancel=cancel,
                )
            except ArchiveSecurityError as exc:
                raise BuildPlanError(f"Extraction security error for {dependency_id}: {exc}") from exc
            return bytes_extracted

    @staticmethod
    def _new_identity() -> Dict[str, str]:
        return {
            "SystemProductName": "MacBookPro15,2",
            "SystemSerialNumber": secrets.token_hex(8).upper(),
            "MLB": secrets.token_hex(12).upper(),
            "SystemUUID": secrets.token_hex(16),
        }

    def _identity_path(
        self,
        plan: BuildPlan,
        dependencies: ResolvedDependencySet,
        identity_reference: Optional[IdentityReference] = None,
    ) -> Path:
        base = self.identity_store_dir
        if identity_reference is not None:
            if not identity_reference.redacted or Path(identity_reference.storage_ref).name != identity_reference.storage_ref:
                raise BuildPlanError("EFI identity reference must be a redacted local filename")
            return base / identity_reference.storage_ref
        key = canonical_json_digest({"plan": plan.canonical_digest(), "dependencies": dependencies.canonical_digest()})
        return base / f"{key}.json"

    @staticmethod
    def _load_identity(path: Path) -> Dict[str, str]:
        service = IdentityService(path.parent)
        try:
            service._assert_private_root()
            service._assert_private_file(path)
        except IdentityServiceError as exc:
            raise BuildPlanError(str(exc)) from exc
        try:
            return service._read_private_values(path)
        except IdentityServiceError as exc:
            raise BuildPlanError(str(exc)) from exc

    @staticmethod
    def _write_private_identity(path: Path, identity: Dict[str, str]) -> None:
        try:
            IdentityService(path.parent).store(identity, storage_ref=path.name, validate_values=False)
        except IdentityServiceError as exc:
            raise BuildPlanError(f"Unable to protect private EFI identity: {exc}") from exc

    @staticmethod
    def _redact_diagnostics(text: str, identity: Optional[Dict[str, Any]]) -> str:
        redacted = text
        for value in (identity or {}).values():
            # PlatformInfo also carries ROM bytes, booleans and integers.  Only
            # identifying text and byte values can appear in tool output;
            # replacing "0" or "False" would corrupt the diagnostic instead.
            if isinstance(value, (bytes, bytearray)) and value:
                for form in (value.hex(), value.hex().upper()):
                    redacted = redacted.replace(form, "<redacted>")
            elif isinstance(value, str) and value:
                redacted = redacted.replace(value, "<redacted>")
        return redacted

    @staticmethod
    def _identity_errors(identity: Dict[str, str]) -> List[str]:
        required = ("SystemProductName", "SystemSerialNumber", "MLB", "SystemUUID")
        errors: List[str] = []
        for key in required:
            value = identity.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{key} is required and must be a non-empty string")
        for key in ("SystemSerialNumber", "MLB"):
            value = identity.get(key, "")
            if value and not re.fullmatch(r"[A-Za-z0-9]{4,32}", value):
                errors.append(f"{key} has an invalid format")
        allowed = {
            "SystemProductName", "SystemSerialNumber", "MLB", "SystemUUID", "ROM",
            "AdviseFeatures", "MaxBIOSVersion", "ProcessorType", "SpoofVendor",
            "SystemMemoryStatus",
        }
        if set(identity) - allowed:
            errors.append("identity contains unsupported fields")
        uuid_value = identity.get("SystemUUID", "")
        if uuid_value and not re.fullmatch(r"[0-9A-Fa-f-]{8,64}", uuid_value):
            errors.append("SystemUUID has an invalid format")
        if identity.get("SystemProductName") != "MacBookPro15,2":
            errors.append("SystemProductName is not permitted by the current identity policy")
        return errors

    @staticmethod
    def _write_config(path: Path, kexts: List[str], drivers: List[str], identity: Dict[str, str], opencore_version: str) -> None:
        config = {
            "#WARNING": "Generated by MacLoader; validate with matching ocvalidate before use.",
            "OC": {"Version": opencore_version},
            "UEFI": {"Drivers": [{"Path": item, "Enabled": True} for item in sorted(set(drivers))]},
            "Kernel": {"Add": [{"BundlePath": item, "Enabled": True} for item in sorted(set(kexts))]},
            "PlatformInfo": {"Generic": dict(identity)},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(config, handle, sort_keys=False)
