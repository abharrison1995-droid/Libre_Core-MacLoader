# MacLoader Implementation Handoff — v0.0.1 → v0.0.4

> Historical handoff, refreshed 2026-09-08. Follow the [active implementation plan](IMPLEMENTATION_PLAN.md): S12 Recovery and removable-media guards are verified and committed, with 250 local tests passing, mypy clean across 72 files and 79.82% branch coverage against a 79% CI gate. G0 remains reopened; packaging evidence, EFI trust/validation, official Recovery acquisition, platform USB adapters, clean-host workflow and physical acceptance remain open. Sequoia is the first qualification target. Neither historical milestone completion nor prototype code establishes shipping readiness.

**Project:** Libre_Core-MacLoader  
**Completed Milestones:**
- **v0.0.1**: ThinkPad T480s hardware detection (`20L7`, `20L8`)
- **v0.0.2**: ThinkPad T480 hardware detection (`20L5`, `20L6`) and Nvidia MX150 variant handling
- **v0.0.3**: Hardware/macOS compatibility reporting (Sonoma, Sequoia, Tahoe) and preliminary BuildPlan
- **v0.0.4**: OpenCore dependency catalog, DAG graph resolver, SHA-256 integrity verification, and local cache

**Date:** 2026-08-29  
**Status:** Complete, 100% test pass rate (67/67 automated tests passing), zero mypy type errors across 55 source files.

---

## 1. Completed Functionality (v0.0.4)

### Dependency Catalog & Schemas (`macloader.database`)
- **Declarative Catalog**: `macloader/database/data/dependencies/catalog.yaml` defines pinned release metadata under policy set `2026.08-a` with exact source URLs, 64-char lowercase hex SHA-256 hashes, file sizes, parent dependencies, and subcomponents.
- **Strict Schema Validation**: `DependencyCatalogSchema` validates top-level structure, duplicate ID prevention, artifact presence, and SHA-256 format. Raises `DatabaseValidationError` on any schema failure.

### Dependency Graph & Topological Sorting (`macloader.dependencies.graph`)
- **Directed Acyclic Graph (DAG)**: `DependencyGraph` tracks prerequisite parent relationships (e.g. `WhateverGreen` -> `Lilu`, `VirtualSMC` -> `Lilu`, `AppleALC` -> `Lilu`, `NVMeFix` -> `Lilu`, `BlueToolFixup` -> `Lilu`).
- **Topological Sorting**: Resolves all transitive prerequisites and orders dependencies so that parent kexts (like `Lilu`) always precede downstream plugins.
- **Cycle Detection**: 3-color DFS cycle detector loudly raises `DependencyCycleError` if a circular dependency is introduced.

### Domain Models (`macloader.domain.dependencies`)
- Typed classes: `DependencySpec`, `DependencyArtifact`, `ArtifactVariant` (`RELEASE`, `DEBUG`), `ResolvedDependency`, and `ResolvedDependencySet`.
- Full dictionary and JSON serialization with explainable provenance (`reason`, `required_by`, `is_transitive`, `subcomponents`).

### Dependency Resolver (`macloader.dependencies.resolver`)
- **Capability-Driven**: Translates `BuildPlan.required_capabilities` directly into required components.
- **Version-Aware Policy**:
  - **macOS Sonoma**: Selects `AirportItlwm` or `itlwm` for Intel WLAN; `AppleALC` for audio.
  - **macOS Sequoia**: Selects `itlwm` for Intel WLAN; `AppleALC` for audio.
  - **macOS Tahoe**: Omits `AppleALC` as a solved solution because Apple removed `AppleHDA.kext`; explicitly records `Tahoe analogue audio` in `unresolved_requirements` with warning. Selects `itlwm` with experimental warning.
  - **Nvidia MX150**: Flags discrete GPU as requiring disabling via ACPI SSDT / boot args; does NOT select an Nvidia driver.
  - **Samsung PM981**: Selects `NVMeFix` (+ transitive `Lilu`) with power stability warning.
  - **Side-Effect-Free**: Pure calculation without any network I/O.

### Downloader, Integrity Verification & Safe Archive Inspection (`macloader.dependencies`)
- **Streaming Downloader (`downloader.py`)**: Streams HTTPS responses into temporary `.part` files while simultaneously computing SHA-256 hashes. Verifies hash against version-controlled manifest before atomic replacement. Hard failure on mismatch (`ChecksumMismatchError`).
- **Cache Manager (`cache.py`)**: Stores verified downloads under `workspace/cache/downloads/` with deterministic filenames and `index.json`. Verifies SHA-256 on every cache check; rejects and cleans up corrupted entries.
- **Archive Safety (`archive.py`)**: Inspects zip archives to protect against directory traversal (`../`), absolute paths, and corrupt archives (`ArchiveSecurityError`).
- **Offline Mode**: Supports `--offline` operation strictly from verified local cache without network attempts.

---

## 2. Pinned Dependency Policy (`2026.08-a`)

| Dependency ID | Project Name | Upstream Repository | Version / Tag | Release SHA-256 Hash |
|---|---|---|---|---|
| `opencore` | OpenCorePkg | acidanthera/OpenCorePkg | `1.0.7` | `2ffab6ebf58c7aefb0bcb3a1a385d207746823d6dd87d44bd666e1286939943e` |
| `lilu` | Lilu | acidanthera/Lilu | `1.7.2` | `53967d7dcfaab01023a33df2e969a89522f13d6654a6a56ac4711b62dabf3ab8` |
| `whatevergreen` | WhateverGreen | acidanthera/WhateverGreen | `1.7.0` | `6d6ffe8334ad60f784a662794e67b2560b79d757d506841dc8ca9994ab39979b` |
| `virtualsmc` | VirtualSMC | acidanthera/VirtualSMC | `1.3.7` | `12f1d379969f926306fa92d94ddbf33b32b31176589dc42089d864a26b31b700` |
| `applealc` | AppleALC | acidanthera/AppleALC | `1.9.7` | `81a8ba79986130e8c845fff595950226cbc30e588f8d37089e467f776469c29d` |
| `intelmausi` | IntelMausi | acidanthera/IntelMausi | `1.0.8` | `cc02ea7e972ead536a51e5f6725e79f121026dbf36a766e97044ef2483fd29bc` |
| `nvmefix` | NVMeFix | acidanthera/NVMeFix | `1.1.3` | `e1d5657ab7ac31f69771708f7b80bf218ab9aa0b8e4c4fe6ff943983037e3dfb` |
| `voodoops2` | VoodooPS2 | acidanthera/VoodooPS2 | `2.3.7` | `d5483298736ba4b3b82c7203d9a46968c668fb7af732b8b22c2096deb0acf83f` |
| `voodooi2c` | VoodooI2C | VoodooI2C/VoodooI2C | `v2.9.1` | `368e5eb60794583c340764813bd7e8244e94d3d3c06ea5b67034addd539487f4` |
| `itlwm` | itlwm | OpenIntelWireless/itlwm | `v2.3.0` | `31ed2e5b4bca92645bfab0a397a1f50212c72a04ea15591c061fe74233e4f82b` |
| `airportitlwm` | AirportItlwm (Sonoma) | OpenIntelWireless/itlwm | `v2.3.0` | `7ef12a8037eb9bc795828c348f4fa0728b8ade57aa0984b037816ef91c2d5119` |
| `intel_bluetooth_firmware`| IntelBluetooth | OpenIntelWireless | `v2.4.0` | `78418daa11f620012fc53ba68cbd643c2642a45f2f96c594fd01aaf2bc68fe2b` |
| `bluetoolfixup` | BrcmPatchRAM | acidanthera/BrcmPatchRAM | `2.7.2` | `e1c1c55347526d031a8ae2fdd1f52efa3019161e497fb38e1cfa809752f8af21` |

---

## 3. Resolver Architecture & Catalog vs Selection

```text
HardwareSnapshot
       ↓
CompatibilityReport
       ↓
BuildPlan.required_capabilities
       ↓
DependencyResolver
  ├── 1. Base Core Selection (OpenCorePkg + VirtualSMC)
  ├── 2. Map Capabilities (e.g. accelerated_intel_uhd_620 -> WhateverGreen)
  ├── 3. Evaluate OS Policy (e.g. Sonoma -> AirportItlwm, Tahoe -> Unresolved Audio)
  ├── 4. DAG Graph Resolution (Pulls in transitive parent Lilu)
  └── 5. Topological Sort (Parents before children: Lilu -> WhateverGreen)
       ↓
ResolvedDependencySet
```

**Catalog ≠ Selection Rule**: The catalog contains all known verified upstream projects. The resolver selects ONLY the minimal set of dependencies required for the target machine and macOS version. It never generates an "install everything" folder.

---

## 4. Useful CLI Commands Available

```bash
# List verified dependency catalog
macloader deps list
macloader deps list --json

# Resolve dependencies without network I/O
macloader deps resolve --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia
macloader deps resolve --fixture tests/fixtures/t480/t480_mx150.json --macos sequoia
macloader deps resolve --fixture tests/fixtures/t480s/t480s_baseline.json --macos tahoe --json -o dependency-lock.json

# Fetch and cache verified dependency artifacts
macloader deps fetch --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia

# Fetch in strict offline mode (cache only)
macloader deps fetch --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia --offline

# Verify SHA-256 integrity of cached dependencies
macloader deps verify --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia

# Inspect or clear local dependency cache
macloader deps cache
macloader deps cache --clear
```

---

## 5. Test Suite & Verification Results

```bash
$ python -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79
============================== 250 passed ==============================

$ python -m mypy macloader tests
Success: no issues found in 72 source files
```

Key test coverage:
- Declarative catalog loading and YAML schema validation.
- Rejection of invalid SHA-256 formats and duplicate dependency IDs.
- DAG transitive resolution and topological sorting.
- Circular dependency cycle detection with loud failure.
- Capability mapping: iGPU -> WhateverGreen + Lilu; PM981 -> NVMeFix + Lilu.
- MX150 handling: ACPI disable note in plan, zero Nvidia kexts selected.
- Target OS policies: Sonoma (AirportItlwm), Sequoia (itlwm), Tahoe (unresolved audio + experimental Wi-Fi).
- SHA-256 matching download, mismatch rejection, and `.part` file cleanup.
- Offline cache hits, corrupted cache entry eviction, and cache stats.
- Archive safety checks preventing directory traversal (`../`) and absolute paths.
- CLI commands: `deps list`, `deps resolve`, `deps fetch`, `deps verify`, `deps cache`.

---

## 6. Unresolved Policies & Intentional Deferrals

- **Tahoe Analogue Audio**: Explicitly kept as an `unresolved_requirement` in the `ResolvedDependencySet`. Apple removed `AppleHDA` in macOS Tahoe, so AppleALC alone is insufficient. Workarounds (root-patching / VoodooHDA) are deferred to v0.0.5.
- **Tahoe Intel Wi-Fi**: Modeled conservatively with warnings regarding legacy network framework shifts in macOS 26.
- **EFI Tree Construction**: In accordance with the spec, v0.0.4 resolves software artifacts and verifies caches. It does NOT generate `config.plist`, compile ACPI `.aml` tables, or construct EFI directory trees.

---

## 7. v0.0.5 Readiness (Next Milestone)

The next implementation agent should implement **v0.0.5 — ThinkPad T480s OpenCore EFI Generator**:
1. Take the verified `ResolvedDependencySet` and local cached archives from v0.0.4.
2. Extract required subcomponents (e.g. `OpenCore.efi`, `OpenRuntime.efi`, `Lilu.kext`, `WhateverGreen.kext`, `VirtualSMC.kext` + `SMCBatteryManager.kext` + `SMCProcessor.kext`, `IntelMausi.kext`, `VoodooPS2Controller.kext`) into an EFI staging directory:
   ```
   EFI/
   ├── BOOT/
   │   └── BOOTx64.efi
   └── OC/
       ├── ACPI/
       ├── Drivers/
       ├── Kexts/
       ├── Tools/
       └── config.plist
   ```
3. Generate compiled ACPI SSDT tables (e.g. `SSDT-PLUG`, `SSDT-EC-USBX`, `SSDT-PNLF`, `SSDT-GPI0`, `SSDT-XOSI`).
4. Generate canonical, validated OpenCore 1.0.7 `config.plist` referencing the exact selected kexts in topological order.
5. Generate unique local SMBIOS identity (MacBookPro15,2 / MacBookPro15,4).

---

## 8. Critical Warnings for Future Agents

> [!WARNING]
> 1. **Do NOT use mutable "latest" URLs.** All dependencies must use the pinned release metadata in `macloader/database/data/dependencies/catalog.yaml`.
> 2. **Do NOT suppress checksum verification.** Every downloaded file and cache entry must match its expected SHA-256 hash.
> 3. **Do NOT download or extract unneeded kexts.** Use only what `DependencyResolver.resolve()` returned.
> 4. **Do NOT claim Tahoe analogue audio is solved by simply dropping AppleALC into EFI.** Tahoe requires explicit workarounds.
> 5. **Do NOT hardcode SMBIOS serial numbers in code or fixtures.** Local random SMBIOS identity generation is mandatory.
