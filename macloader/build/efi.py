"""Build a guarded EFI tree from an actionable plan and verified artifacts."""

from dataclasses import dataclass
import json
import plistlib
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Dict, Iterable, List, Optional

from macloader.dependencies.archive import safe_extract_zip, validate_zip_archive
from macloader.dependencies.cache import compute_file_sha256
from macloader.domain.build_plan import BuildPlan
from macloader.domain.contracts import BuildManifest, CONTRACT_SCHEMA_VERSION, IdentityReference, ValidationReport
from macloader.domain.dependencies import ResolvedDependencySet
from macloader.exceptions import BuildPlanError


@dataclass
class EfiBuildResult:
    output_dir: Path
    manifest: BuildManifest
    validation: ValidationReport
    identity: IdentityReference


class EfiBuilder:
    """Construct only from an actionable, complete dependency lock."""

    def build(
        self,
        plan: BuildPlan,
        dependencies: ResolvedDependencySet,
        artifact_paths: Dict[str, Path],
        output_dir: Path,
        fake_identity: Optional[Dict[str, str]] = None,
    ) -> EfiBuildResult:
        if not plan.is_actionable or not plan.build_ready:
            reasons = "; ".join(plan.unresolved_requirements) or "hardware support state is not actionable"
            raise BuildPlanError(f"EFI build is blocked until the plan is actionable and build-ready: {reasons}")
        if not dependencies.is_complete or dependencies.unresolved_requirements:
            reasons = "; ".join(dependencies.unresolved_requirements) or "dependency set is incomplete"
            raise BuildPlanError(f"EFI build is blocked by unresolved dependency or policy requirements: {reasons}")
        if not dependencies.catalog_digest or not dependencies.policy_version:
            raise BuildPlanError("EFI build requires a complete catalog lock with policy identity")
        if plan.target_model != dependencies.target_model or plan.target_macos.lower() != dependencies.target_macos.lower():
            raise BuildPlanError("EFI build plan and dependency lock target do not match")
        if output_dir.exists() and any(output_dir.iterdir()):
            raise BuildPlanError(f"Refusing to overwrite a non-empty EFI output directory: {output_dir}")

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
                if archive_path.stat().st_size != dependency.artifact.size_bytes or compute_file_sha256(archive_path) != dependency.artifact.sha256.lower():
                    raise BuildPlanError(f"Verified archive changed or does not match the lock for {dependency.dependency_id}")
                spec_components = list(dependency.subcomponents)
                self._extract_selected(archive_path, spec_components, efi_root, dependency.dependency_id)
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
            identity_ref = IdentityReference(CONTRACT_SCHEMA_VERSION, str(identity_path), redacted=True)
            self._write_config(efi_root / "OC" / "config.plist", kexts, drivers, identity_data)
            validation = self.validate_tree(staging)
            if validation.status != "VALID":
                raise BuildPlanError("Generated EFI failed structural validation: " + "; ".join(validation.errors))

            manifest = BuildManifest(
                schema_version=CONTRACT_SCHEMA_VERSION,
                build_digest=dependencies.canonical_digest(),
                target_model=plan.target_model,
                target_macos=plan.target_macos,
                artifact_lock_digest=dependencies.to_artifact_lock().digest,
                validation_report="VALID",
                output_paths={"efi": "EFI", "licenses": "LICENSES"},
            )
            (staging / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
            staging.replace(output_dir)
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

    def _extract_selected(self, archive_path: Path, components: Iterable[str], efi_root: Path, dependency_id: str) -> None:
        with tempfile.TemporaryDirectory(prefix=f"macloader-{dependency_id}-") as temp_name:
            temp = Path(temp_name)
            snapshot = temp / "archive.zip"
            shutil.copyfile(archive_path, snapshot, follow_symlinks=False)
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
    def _write_config(path: Path, kexts: List[str], drivers: List[str], identity: Dict[str, str]) -> None:
        config = {
            "#WARNING": "Generated by MacLoader; validate with matching ocvalidate before use.",
            "OC": {"Version": "1.0.7"},
            "UEFI": {"Drivers": [{"Path": item, "Enabled": True} for item in sorted(set(drivers))]},
            "Kernel": {"Add": [{"BundlePath": item, "Enabled": True} for item in sorted(set(kexts))]},
            "PlatformInfo": {"Generic": dict(identity)},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(config, handle, sort_keys=False)
