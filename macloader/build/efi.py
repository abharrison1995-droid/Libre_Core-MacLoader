"""Build a guarded EFI tree from an actionable plan and verified artifacts."""

from dataclasses import dataclass
import json
import getpass
import plistlib
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional

from macloader.dependencies.archive import safe_extract_zip, validate_zip_archive
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
from macloader.exceptions import BuildPlanError


@dataclass
class EfiBuildResult:
    output_dir: Path
    manifest: BuildManifest
    validation: ValidationReport
    identity: IdentityReference


class EfiBuilder:
    """Construct only from an actionable, complete dependency lock."""

    def __init__(self, db: Optional[Database] = None, identity_store_dir: Optional[Path] = None):
        self.db = db or get_database()
        self.identity_store_dir = identity_store_dir

    def build(
        self,
        plan: BuildPlan,
        dependencies: ResolvedDependencySet,
        artifact_paths: Dict[str, Path],
        output_dir: Path,
        fake_identity: Optional[Dict[str, str]] = None,
        toolchain: Optional[ToolchainSelection] = None,
    ) -> EfiBuildResult:
        if not plan.support_state.is_usable or plan.unresolved_requirements:
            reasons = "; ".join(plan.unresolved_requirements) or "hardware support state is not actionable"
            raise BuildPlanError(f"EFI build is blocked until the plan is actionable and build-ready: {reasons}")
        if toolchain is None:
            raise BuildPlanError("EFI build requires a validated toolchain selection")
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
        try:
            efi_root = staging / "EFI"
            for relative in ("BOOT", "OC/ACPI", "OC/Drivers", "OC/Kexts", "OC/Tools"):
                (efi_root / relative).mkdir(parents=True, exist_ok=True)
            licenses = staging / "LICENSES"
            licenses.mkdir()

            kexts: List[str] = []
            drivers: List[str] = []
            for dependency in dependencies.resolved_dependencies:
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
                )
                for component in spec_components:
                    name = Path(component).name
                    if name.endswith(".kext"):
                        kexts.append(name)
                    elif component.startswith("EFI/OC/Drivers/") and name.endswith(".efi"):
                        drivers.append(name)
                (licenses / f"{dependency.dependency_id}.txt").write_text(
                    f"{dependency.project_name}\nLicense: recorded in the verified catalog\n", encoding="utf-8"
                )

            identity_path = self._identity_path(plan, dependencies)
            stored_identity = self._load_identity(identity_path) if identity_path.is_file() else None
            if fake_identity is not None and stored_identity is not None and fake_identity != stored_identity:
                raise BuildPlanError("Explicit EFI identity conflicts with the stored identity for this build scope")
            identity_preexisting = stored_identity is not None
            identity_data = stored_identity or (fake_identity if fake_identity is not None else self._new_identity())
            identity_errors = self._identity_errors(identity_data)
            if identity_errors:
                raise BuildPlanError("Invalid EFI identity: " + "; ".join(identity_errors))
            self._write_config(efi_root / "OC" / "config.plist", kexts, drivers, identity_data, toolchain.opencore_version)
            validation = self.validate_tree(staging, toolchain=toolchain, identity=identity_data)
            if validation.status != "VALID":
                raise BuildPlanError("Generated EFI failed structural validation: " + "; ".join(validation.errors))

            output_digest = self._tree_digest(staging)
            manifest = BuildManifest(
                schema_version=CONTRACT_SCHEMA_VERSION,
                build_digest=canonical_json_digest({
                    "plan_digest": plan.canonical_digest(),
                    "dependency_digest": dependencies.canonical_digest(),
                    "toolchain_digest": toolchain.digest,
                    "identity_digest": canonical_json_digest(identity_data),
                    "output_digest": output_digest,
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                }),
                target_model=plan.target_model,
                target_macos=plan.target_macos,
                artifact_lock_digest=dependencies.to_artifact_lock().digest,
                validation_report="VALID",
                toolchain_digest=toolchain.digest,
                identity_digest=canonical_json_digest(identity_data),
                output_digest=output_digest,
                output_paths={"efi": "EFI", "licenses": "LICENSES"},
            )
            (staging / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
            if not identity_preexisting:
                self._write_private_identity(identity_path, identity_data)
            staging.replace(output_dir)
            identity_ref = IdentityReference(CONTRACT_SCHEMA_VERSION, str(identity_path), redacted=True)
            return EfiBuildResult(output_dir, manifest, validation, identity_ref)
        except Exception:
            if "identity_path" in locals() and not identity_preexisting:
                try:
                    identity_path.unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def validate_tree(self, root: Path, toolchain: Optional[ToolchainSelection] = None, timeout_seconds: float = 30.0, identity: Optional[Dict[str, str]] = None) -> ValidationReport:
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
                    if identity_for_redaction is None and isinstance(generic, dict) and all(isinstance(key, str) and isinstance(value, str) for key, value in generic.items()):
                        identity_for_redaction = generic
                    if not isinstance(generic, dict) or self._identity_errors(generic):
                        errors.append("PlatformInfo.Generic does not contain a valid identity")
                    elif identity is not None and generic != identity:
                        errors.append("PlatformInfo.Generic does not match the selected private identity")
                    if toolchain is not None:
                        configured_version = config.get("OC", {}).get("Version") if isinstance(config.get("OC"), dict) else None
                        if configured_version != toolchain.opencore_version:
                            errors.append("config.plist OpenCore version does not match the selected toolchain")
        except (OSError, plistlib.InvalidFileException) as exc:
            errors.append(f"Invalid config.plist: {exc}")
        if errors:
            return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, errors, warnings)
        output_digest = self._tree_digest(root)
        checks["output_digest"] = output_digest
        manifest_path = root / "manifest.json"
        if manifest_path.is_file() and not manifest_path.is_symlink():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_data.get("output_digest") != output_digest:
                    return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID", None, checks, ["Published output changed after validation"], warnings)
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
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
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
    def _tree_digest(root: Path) -> str:
        records: List[Dict[str, Any]] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "manifest.json"):
            records.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": compute_file_sha256(path)})
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
                if name in seen:
                    errors.append(f"Duplicate {label} configuration entry: {name}")
                seen.add(name)
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
        return errors

    def _extract_selected(
        self,
        archive_path: Path,
        components: Iterable[str],
        efi_root: Path,
        dependency_id: str,
        expected_sha256: str,
        expected_size: int,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=f"macloader-{dependency_id}-") as temp_name:
            temp = Path(temp_name)
            snapshot = temp / "archive.zip"
            shutil.copyfile(archive_path, snapshot, follow_symlinks=False)
            if snapshot.stat().st_size != expected_size or compute_file_sha256(snapshot) != expected_sha256:
                raise BuildPlanError(f"Verified archive changed or does not match the lock for {dependency_id}")
            names = validate_zip_archive(snapshot)
            selected: List[str] = []
            matches: List[tuple[str, str]] = []
            for component in components:
                candidates = [name for name in names if name.rstrip("/") == component or name.rstrip("/").endswith("/" + component)]
                if not candidates:
                    nested = [name for name in names if ("/" + component + "/") in name]
                    if nested:
                        nested_name = min(nested, key=len)
                        prefix = nested_name.split("/" + component + "/", 1)[0] + "/"
                        candidates = [prefix + component + "/"]
                if not candidates:
                    raise BuildPlanError(f"Component {component} is missing from {archive_path.name}")
                candidate = min(candidates, key=len).rstrip("/")
                prefix = candidate[: -len(component)] if candidate.endswith(component) else ""
                matches.append((candidate, prefix))
                selected.extend(name for name in names if name.startswith(prefix + component))
            expanded = temp / "expanded"
            safe_extract_zip(snapshot, expanded, sorted(set(selected)))
            for candidate, prefix in matches:
                source = expanded / (prefix + candidate[len(prefix):])
                component = candidate[len(prefix):].rstrip("/")
                destination = efi_root.parent / component if component.startswith("EFI/") else efi_root / "OC" / "Kexts" / Path(component).name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, destination, dirs_exist_ok=True)
                elif source.is_file():
                    shutil.copy2(source, destination)

    @staticmethod
    def _new_identity() -> Dict[str, str]:
        return {
            "SystemProductName": "MacBookPro15,2",
            "SystemSerialNumber": secrets.token_hex(8).upper(),
            "MLB": secrets.token_hex(12).upper(),
            "SystemUUID": secrets.token_hex(16),
        }

    def _identity_path(self, plan: BuildPlan, dependencies: ResolvedDependencySet) -> Path:
        base = self.identity_store_dir or (Path.home() / "AppData" / "Local" / "MacLoader" / "identities" if os.name == "nt" else Path.home() / ".local" / "share" / "macloader" / "identities")
        key = canonical_json_digest({"plan": plan.canonical_digest(), "dependencies": dependencies.canonical_digest()})
        return base / f"{key}.json"

    @staticmethod
    def _load_identity(path: Path) -> Dict[str, str]:
        if path.is_symlink():
            raise BuildPlanError("Stored EFI identity path must not be a symlink")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BuildPlanError(f"Stored EFI identity cannot be read: {exc}") from exc
        if not isinstance(data, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
            raise BuildPlanError("Stored EFI identity has an invalid shape")
        return data

    @staticmethod
    def _write_private_identity(path: Path, identity: Dict[str, str]) -> None:
        if path.is_symlink():
            raise BuildPlanError("Refusing to overwrite a symlink at the private EFI identity path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(identity, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError as exc:
            raise BuildPlanError(f"Unable to protect private EFI identity: {exc}") from exc
        if os.name == "nt":
            account = getpass.getuser()
            result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(R,W)"], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise BuildPlanError("Unable to apply private Windows ACL to EFI identity")

    @staticmethod
    def _redact_diagnostics(text: str, identity: Optional[Dict[str, str]]) -> str:
        redacted = text
        for value in (identity or {}).values():
            if value:
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
        if set(identity) - set(required):
            errors.append("identity contains unsupported fields")
        for key in ("SystemSerialNumber", "MLB"):
            value = identity.get(key, "")
            if value and not re.fullmatch(r"[A-Za-z0-9]{4,32}", value):
                errors.append(f"{key} has an invalid format")
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
