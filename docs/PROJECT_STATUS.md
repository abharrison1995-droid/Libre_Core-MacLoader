# Current project status

Updated 2026-11-17 at local `main` `4d0ad32` (`origin/main` is at `78bdc4b`). This is the status source of truth for the software and its qualification boundaries. Active work follows the [T480s first-install fix plan](T480S_FIRST_INSTALL_FIX_PLAN.md); its "Progress" section says which chunk is next.

## What the software does

MacLoader is version **0.0.4**, requires Python 3.11+, and provides a Click CLI and Textual TUI over shared workflow services, plus a guided autoloader (`macloader`, `macloader autoload`).

- Detects hardware through Linux and Windows providers or JSON fixtures, normalizes and sanitizes the snapshot, then evaluates model/component compatibility and creates an actionable BuildPlan.
- Loads model, component, macOS, profile, dependency, Recovery, and toolchain policy from bundled YAML data.
- Creates, edits, imports, exports, reviews, and resumes schema-driven configurations. It binds selections to the hardware snapshot, validates imported machine evidence, derives the effective profile, and reports blockers through preflight.
- Resolves pinned dependencies, downloads and caches verified artifacts, supports offline reuse, and records provenance.
- Imports and validates private ACPI captures; prepares EFI trees from the reviewed profile, dependency lock, trusted toolchain, and private identity reference; runs structural checks and matching `ocvalidate`; emits redacted build/validation manifests.
- Provides Recovery policy/discovery/acquisition/cache operations with exact-target and Apple-signed chunklist checks, plus removable-device discovery and guarded write/readback contracts.

The CLI help is the authoritative command list. Lower-level APIs and test adapters do not grant qualification by themselves.

## Current qualification scope

The guided EFI candidate is narrowly scoped to **ThinkPad T480s 20L8, BIOS N22ET85W 1.62, Sequoia 15.0 build 24A335**. Its support state is `EXPERIMENTAL`. Physical boot, Recovery, installation, and hardware acceptance have not been completed. No model is `SUPPORTED` by MacLoader acceptance. Do not infer other T480s machine types, the T480, or other macOS builds from this candidate.

## Recent progress (fix plan, 2026-11-17)

- **Windows hardware detection (Phase 1):** software network/Bluetooth adapters, the WWAN modem and Intel Display Audio no longer appear as unknown hardware; NVMe drives reported by Windows as SCSI are recognized and USB disks are excluded from internal storage; input devices are classified in a fixed order; PCI devices get their slot from numeric bus/address properties (needed by every Windows EFI build); failed Windows queries show a short warning.
- **Guided session engine (Phase 2):** the pre-write hardware check compares fixed machine identity only; relaunching with the installer stick attached keeps finished work; a failed session save restores its revision; Retry re-probes hardware for hardware and binding blockers; an unfinished USB write is flagged after relaunch; unreadable session files and missing machine identity have their own codes.
- **Test baseline:** 753 passed, 16 skipped, 1 expected failure (an external USB keyboard during a session still invalidates earlier steps, by decision); mypy clean. Two UI tests are intermittent; fix-plan chunk P2.7 addresses them.

## Open gates

1. **Owner facts (fix plan Phase 0):** the pinned `Sample.plist` defaults, a fresh ACPI capture on the final BIOS settings, sanitized Windows device dumps, and the BIOS settings record are needed before Phase 3 goes past its first chunk.
2. **EFI that boots (Phases 3-4):** generated SSDTs are not yet bound to the captured DSDT, the HFS+ driver and reviewed quirk/security settings are missing, and the kext set still depends on detection.
3. **USB port mapping from Windows (Phase 5):** the guided wizard still refuses Windows; a Linux live session is the fallback.
4. **Recovery (Phase 6):** exact 24A335 is unproven; the smoke path needs the GET/HTTPS/timeout fixes.
5. **Physical media (Phases 7-8):** the Windows writer needs the safety fixes, then the owner's 15-case physical qualification; the approval catalog is empty and production writes are disabled.
6. **Install (Phase 10):** the pre-install gate, rescue stick and install procedure are planned, not built.

No BIOS change, USB write, internal-disk operation, real identity generation, or macOS installation is authorized by this status page.

## Source map

- [T480s first-install fix plan](T480S_FIRST_INSTALL_FIX_PLAN.md): the active plan, progress and decisions.
- [Architecture](ARCHITECTURE.md): implemented modules and data flow.
- [Autoloader implementation status](AUTOLOADER_V1_IMPLEMENTATION_STATUS.md): the guided implementation by phase.
- [Support matrix](SUPPORT_MATRIX.md): what is modeled versus physically accepted.
- [Implementation ledger](IMPLEMENTATION_LEDGER.md): completed software and hosted run evidence.
- [First-install runbook](T480S_FIRST_INSTALL_RUNBOOK.md): physical checkpoints (being rewritten by fix-plan chunk P11.4a).
- [Hardware acceptance](HARDWARE_ACCEPTANCE.md): required real-machine evidence.
- [Research ledger](RESEARCH_LEDGER.md): policy sources and dated research records.
- [Decisions](DECISIONS.md) and the [engineering specification](ENGINEERING_SPEC_V0.1.md): design records.
