"""Build a guarded EFI tree from an actionable plan and verified artifacts."""

from dataclasses import dataclass
import json
import plistlib
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Any, Dict, Iterable, List, Optional

from macloader.dependencies.archive import safe_extract_zip, validate_zip_archive
from macloader.dependencies.cache import compute_file_sha256
from macloader.dependencies.resolver import DependencyResolver
from macloader.database.loader import Database, get_database
from macloader.domain.build_plan import BuildPlan
from macloader.domain.contracts import BuildManifest, CONTRACT_SCHEMA_VERSION, IdentityReference, ToolchainSelection, ValidationReport, _digest
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

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_database()

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
                    elif name.endswith(".efi"):
                        drivers.append(name)
                (licenses / f"{dependency.dependency_id}.txt").write_text(
                    f"{dependency.project_name}\nLicense: recorded in the verified catalog\n", encoding="utf-8"
                )

            identity_data = fake_identity or self._new_identity()
            identity_path = staging / ".identity.private.json"
            identity_path.write_text(json.dumps(identity_data, indent=2), encoding="utf-8")
            self._write_config(efi_root / "OC" / "config.plist", kexts, drivers, identity_data, toolchain.opencore_version)
            validation = self.validate_tree(staging)
            if validation.status != "VALID":
                raise BuildPlanError("Generated EFI failed structural validation: " + "; ".join(validation.errors))

            manifest = BuildManifest(
                schema_version=CONTRACT_SCHEMA_VERSION,
                build_digest=_digest({
                    "plan_digest": plan.canonical_digest(),
                    "dependency_digest": dependencies.canonical_digest(),
                    "toolchain_digest": toolchain.digest,
                    "identity_digest": _digest(identity_data),
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                }),
                target_model=plan.target_model,
                target_macos=plan.target_macos,
                artifact_lock_digest=dependencies.to_artifact_lock().digest,
                validation_report="VALID",
                toolchain_digest=toolchain.digest,
                identity_digest=_digest(identity_data),
                output_paths={"efi": "EFI", "licenses": "LICENSES"},
            )
            (staging / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
            staging.replace(output_dir)
            identity_ref = IdentityReference(CONTRACT_SCHEMA_VERSION, str(output_dir / ".identity.private.json"), redacted=True)
            return EfiBuildResult(output_dir, manifest, validation, identity_ref)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def validate_tree(self, root: Path) -> ValidationReport:
        required = [
            root / "EFI" / "BOOT" / "BOOTx64.efi",
            root / "EFI" / "OC" / "OpenCore.efi",
            root / "EFI" / "OC" / "Drivers" / "OpenRuntime.efi",
            root / "EFI" / "OC" / "config.plist",
        ]
        errors = [f"Missing required output: {path.relative_to(root)}" for path in required if not path.is_file()]
        try:
            if (root / "EFI" / "OC" / "config.plist").is_file():
                with (root / "EFI" / "OC" / "config.plist").open("rb") as handle:
                    plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as exc:
            errors.append(f"Invalid config.plist: {exc}")
        return ValidationReport(CONTRACT_SCHEMA_VERSION, "INVALID" if errors else "VALID", None, {"structure": "PASS" if not errors else "FAIL"}, errors)

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
