"""Machine-bound ACPI import, review, and generated-source compilation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Dict, Iterable, List, Optional, Tuple

from macloader.domain.contracts import canonical_json_digest
from macloader.exceptions import BuildPlanError


ACPI_HEADER_SIZE = 36
TABLE_NAMES = ("dsdt.dat", "ssdt.dat", *tuple(f"ssdt{i}.dat" for i in range(1, 12)))


@dataclass(frozen=True)
class AcpiSourceResult:
    file_name: str
    status: str
    warnings: int
    errors: int
    diagnostic_digest: str


@dataclass(frozen=True)
class AcpiBuildResult:
    output_files: Tuple[str, ...]
    source_evidence_digest: str
    generated_digest: str
    tool_digest: str
    diagnostics: Tuple[AcpiSourceResult, ...]
    qualification_path: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": "1",
            "source_evidence_digest": self.source_evidence_digest,
            "generated_digest": self.generated_digest,
            "tool_digest": self.tool_digest,
            "qualification_path": self.qualification_path,
            "table_count": sum(item.file_name in TABLE_NAMES for item in self.diagnostics),
            "generated_files": list(self.output_files),
            "diagnostics": [item.__dict__ for item in self.diagnostics],
        }


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AcpiProcessor:
    """Process the exact private capture without publishing raw ACPI tables."""

    def __init__(self, iasl_path: Path, iasl_sha256: str, work_root: Optional[Path] = None) -> None:
        self.iasl_path = Path(iasl_path).resolve()
        self.iasl_sha256 = iasl_sha256.lower()
        self.work_root = Path(work_root or Path.cwd() / "workspace" / "p4-acpi").resolve()

    def build(
        self,
        private_capture_root: Path,
        output_dir: Path,
        *,
        expected_bios_binding: str,
        expected_evidence_digest: Optional[str] = None,
        allow_oem_compile_failure: bool = True,
    ) -> AcpiBuildResult:
        self._verify_tool()
        capture = Path(private_capture_root).resolve()
        table_dir = capture / "PRIVATE-ACPI"
        if not table_dir.is_dir() or table_dir.is_symlink():
            raise BuildPlanError("Private ACPI capture directory is missing or unsafe")
        table_paths = self._find_tables(table_dir)
        source_metadata: List[Dict[str, object]] = []
        for path in table_paths:
            source_metadata.append(self._validate_table(path))
        source_evidence_digest = canonical_json_digest({
            "bios_binding": expected_bios_binding,
            "tables": source_metadata,
            "capture_scope": "private-acpi-dsdt-plus-eleven-ssdt",
        })
        if expected_evidence_digest is not None and expected_evidence_digest != source_evidence_digest:
            raise BuildPlanError("Private ACPI evidence digest does not match the accepted machine-bound evidence")

        run_dir = self.work_root / source_evidence_digest
        disassembled = run_dir / "disassembled"
        raw_logs = run_dir / "raw-diagnostics"
        raw_compiled = run_dir / "raw-compiled"
        generated_sources = run_dir / "generated-sources"
        generated_aml = run_dir / "generated-aml"
        for directory in (disassembled, raw_logs, raw_compiled, generated_sources, generated_aml):
            directory.mkdir(parents=True, exist_ok=True)

        diagnostics: List[AcpiSourceResult] = []
        for path in table_paths:
            stem = path.stem
            prefix = disassembled / stem
            diagnostic_path = raw_logs / f"{stem}.txt"
            result = self._run(
                ["-d", "-p", str(prefix), str(path)],
                diagnostic_path,
            )
            dsl_path = prefix.with_suffix(".dsl")
            if result[0] != 0 or not dsl_path.is_file():
                raise BuildPlanError(f"Pinned iasl could not disassemble ACPI source {stem}")
            dsl_text = dsl_path.read_text(encoding="utf-8", errors="replace")
            if "LENOVO" not in dsl_text or "DefinitionBlock" not in dsl_text:
                raise BuildPlanError(f"ACPI source {stem} is not the reviewed Lenovo table set")
            compile_result = self._run(
                ["-tc", "-p", str((raw_compiled / stem)), str(dsl_path)],
                raw_logs / f"{stem}-compile.txt",
            )
            if compile_result[0] != 0 and (stem != "dsdt" or not allow_oem_compile_failure):
                raise BuildPlanError(f"Pinned iasl rejected reviewed ACPI source {stem}")
            source_diagnostic = self._diagnostic(
                path.name.lower(),
                result[1] + "\n" + compile_result[1],
                result[2] + "\n" + compile_result[2],
            )
            if compile_result[0] != 0 and stem == "dsdt":
                source_diagnostic = AcpiSourceResult(
                    file_name=source_diagnostic.file_name,
                    status="oem-compile-diagnostic",
                    warnings=source_diagnostic.warnings,
                    errors=source_diagnostic.errors,
                    diagnostic_digest=source_diagnostic.diagnostic_digest,
                )
            elif result[0] == 0 and compile_result[0] == 0:
                source_diagnostic = AcpiSourceResult(
                    file_name=source_diagnostic.file_name,
                    status="passed",
                    warnings=source_diagnostic.warnings,
                    errors=source_diagnostic.errors,
                    diagnostic_digest=source_diagnostic.diagnostic_digest,
                )
            diagnostics.append(source_diagnostic)

        generated_specs = self._generated_sources(expected_bios_binding)
        generated_records: List[Dict[str, object]] = []
        generated_diagnostics: List[AcpiSourceResult] = []
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, source in generated_specs.items():
            source_path = generated_sources / f"{name}.dsl"
            source_path.write_text(source, encoding="utf-8", newline="\n")
            prefix = generated_aml / name
            rc, stdout, stderr = self._run(
                ["-tc", "-p", str(prefix), str(source_path)],
                raw_logs / f"{name}-compile.txt",
            )
            if rc != 0 or not prefix.with_suffix(".aml").is_file():
                raise BuildPlanError(f"Reviewed generated ACPI source failed compilation: {name}")
            aml = prefix.with_suffix(".aml")
            destination = output_dir / aml.name
            shutil.copyfile(aml, destination)
            generated_records.append({
                "name": aml.name,
                "size_bytes": aml.stat().st_size,
                "sha256": _digest_file(aml),
                "diagnostic_digest": canonical_json_digest({"stdout": stdout, "stderr": stderr}),
            })
            generated_diagnostic = self._diagnostic(aml.name, stdout, stderr)
            generated_diagnostics.append(AcpiSourceResult(
                file_name=generated_diagnostic.file_name,
                status="passed",
                warnings=generated_diagnostic.warnings,
                errors=generated_diagnostic.errors,
                diagnostic_digest=generated_diagnostic.diagnostic_digest,
            ))

        generated_digest = canonical_json_digest({
            "bios_binding": expected_bios_binding,
            "sources": generated_records,
        })
        safe_diagnostics = tuple(diagnostics + generated_diagnostics)
        build_result = AcpiBuildResult(
            output_files=tuple(str(item["name"]) for item in generated_records),
            source_evidence_digest=source_evidence_digest,
            generated_digest=generated_digest,
            tool_digest=self.iasl_sha256,
            diagnostics=safe_diagnostics,
            qualification_path="private-input-reviewed-generated-sources",
        )
        (output_dir.parent / "P4-ACPI-qualification.json").write_text(
            json.dumps(build_result.to_dict(), indent=2), encoding="utf-8"
        )
        return build_result

    def _verify_tool(self) -> None:
        if self.iasl_path.is_symlink() or not self.iasl_path.is_file():
            raise BuildPlanError("Pinned iasl is missing or unsafe")
        if _digest_file(self.iasl_path) != self.iasl_sha256:
            raise BuildPlanError("Pinned iasl digest does not match the trusted toolchain")
        rc, stdout, stderr = self._run(["-v"], self.work_root / "iasl-version.txt")
        if rc != 0 or "20260408" not in stdout + stderr:
            raise BuildPlanError("Pinned iasl version does not match ACPICA 20260408")

    @staticmethod
    def _find_tables(table_dir: Path) -> Tuple[Path, ...]:
        paths = {path.name.lower(): path for path in table_dir.glob("*.dat")}
        missing = [name for name in TABLE_NAMES if name not in paths]
        if missing or len(paths) != len(TABLE_NAMES):
            raise BuildPlanError("Private ACPI evidence must contain exactly one DSDT and eleven SSDTs")
        return tuple(paths[name] for name in TABLE_NAMES)

    @staticmethod
    def _validate_table(path: Path) -> Dict[str, object]:
        data = path.read_bytes()
        if len(data) < ACPI_HEADER_SIZE:
            raise BuildPlanError(f"ACPI table is truncated: {path.stem}")
        signature = data[:4].decode("ascii", errors="replace")
        declared_length = int.from_bytes(data[4:8], "little")
        if declared_length != len(data):
            raise BuildPlanError(f"ACPI table length does not match the private capture: {path.stem}")
        if signature not in {"DSDT", "SSDT"} or sum(data) % 256 != 0:
            raise BuildPlanError(f"ACPI table signature or checksum is invalid: {path.stem}")
        return {
            "name": path.name.lower(),
            "signature": signature,
            "length": declared_length,
            "sha256": _digest_file(path),
        }

    @staticmethod
    def _diagnostic(name: str, stdout: str, stderr: str) -> AcpiSourceResult:
        text = (stdout + "\n" + stderr).strip()
        return AcpiSourceResult(
            file_name=name,
            status="passed" if "Error" not in text and "failed" not in text.lower() else "diagnostic-failure",
            warnings=len(re.findall(r"\bWarning\b", text, flags=re.IGNORECASE)),
            errors=len(re.findall(r"\bError\b", text, flags=re.IGNORECASE)),
            diagnostic_digest=canonical_json_digest({"diagnostics": text}),
        )

    def _run(self, args: List[str], diagnostic_path: Path) -> Tuple[int, str, str]:
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [str(self.iasl_path), *args],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            diagnostic_path.write_text("iasl timed out", encoding="utf-8")
            return 124, "", "iasl timed out"
        except OSError as exc:
            raise BuildPlanError(f"Pinned iasl execution failed: {exc}") from exc
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        diagnostic_path.write_text((stdout + "\n" + stderr)[:16384], encoding="utf-8")
        return completed.returncode, stdout, stderr

    @staticmethod
    def _generated_sources(bios_binding: str) -> Dict[str, str]:
        # These are deliberately small, reviewed additions.  The OEM DSDT and
        # SSDTs are never copied into the generated EFI.
        return {
            "SSDT-PLUG-T480S": f'''DefinitionBlock ("", "SSDT", 2, "MLDR", "PLUGT48S", 0x00000001)
{{
    // Machine-bound to Lenovo T480s 20L8, BIOS {bios_binding}.
    External (\\_PR.PR00, ProcessorObj)
    Scope (\\_PR.PR00)
    {{
        Name (PLUG, One)
    }}
}}
''',
            "SSDT-PNLF-T480S": f'''DefinitionBlock ("", "SSDT", 2, "MLDR", "PNLFT48S", 0x00000001)
{{
    // FHD non-touch internal panel policy; machine-bound to BIOS {bios_binding}.
    Scope (\\_SB)
    {{
        Device (PNLF)
        {{
            Name (_HID, "APP0002")
            Name (_CID, "APP0002")
            Name (_UID, 0x13)
            Name (_STA, 0x0B)
        }}
    }}
}}
''',
            "SSDT-USBX-T480S": f'''DefinitionBlock ("", "SSDT", 2, "MLDR", "USBXT48S", 0x00000001)
{{
    // Power properties only. This is not a complete USB port map.
    // USB-C logical correlation remains unresolved for BIOS {bios_binding}.
    External (\\_SB.PCI0.XHC, DeviceObj)
    Scope (\\_SB.PCI0.XHC)
    {{
        Device (USBX)
        {{
            Name (_ADR, Zero)
            Name (_STA, 0x0B)
        }}
    }}
}}
''',
        }
