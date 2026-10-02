# Remaining implementation and qualification plan

Updated 2026-10-02. This plan follows the current code review in [Project status](PROJECT_STATUS.md). The earlier S01–S15 and G0–G7 progress tables were snapshots from September and have been removed to prevent old checkboxes and test counts from being mistaken for current status.

## Completed software scope

The shared configuration workflow, EFI build/validation path, pinned toolchain/dependency services, Recovery controls, and non-destructive media planning are implemented. The defects tracked in the September readiness reviews have corresponding repairs and regression coverage in the current source. See [Implementation ledger](IMPLEMENTATION_LEDGER.md) for the concise component summary and provenance of the previously recorded verification run.

## Remaining gates, in order

1. **Establish exact Recovery.** Obtain an Apple-authoritative authenticated relationship to Sequoia 15.0 build 24A335, or an Apple-signed Recovery payload independently bound to it. Preserve the frozen target unless the user explicitly selects a change. Until then, acquisition and all target-dependent physical stages stay blocked.
2. **Refresh reference-machine evidence.** On the actual T480s 20L8 / BIOS N22ET85W 1.62, create a configuration and privately capture/import the complete ACPI set and physical USB observations. Resolve or retain USB-C correlation as unknown; do not invent a complete map.
3. **Complete private preparation.** At the deliberate workflow step, create/reuse the machine-associated SMBIOS identity; resolve the exact dependency lock; build with the verified host toolchain; review the generated manifest and matching validator report.
4. **Qualify removable media.** Follow the accepted Windows-first, Linux-second media qualification order in [ADR-007](DECISIONS.md#adr-007--guided-t480s-configuration-workflow). Begin with disposable images, then a separately checkpointed sacrificial device with complete readback and failure invalidation. Do not enable a backend based on mocks alone.
5. **Perform physical acceptance.** First boot only to the OpenCore picker and Recovery with internal storage untouched. Installation requires a separate confirmation naming the exact target disk. Complete the [hardware acceptance checklist](HARDWARE_ACCEPTANCE.md) before any support promotion.
6. **Finish release evidence.** Re-establish clean-install, package, host-matrix, license/notice, recovery, known-limitations, and reproducibility evidence on the candidate revision; update [Support matrix](SUPPORT_MATRIX.md) only from recorded results.

## Stop conditions

- Do not substitute a different OS release/build when Recovery cannot prove the requested target.
- Do not write a physical USB until its backend and the sacrificial-device campaign are qualified.
- Do not change firmware, erase/partition an internal disk, or install macOS without a fresh checkpoint for that exact action.
- Keep `SUPPORTED` reserved for the exact scope that passed physical acceptance.

The technical design decisions remain in [DECISIONS.md](DECISIONS.md), [ARCHITECTURE.md](ARCHITECTURE.md), and the [engineering specification](ENGINEERING_SPEC_V0.1.md). The detailed first-install sequence is in the [runbook](T480S_FIRST_INSTALL_RUNBOOK.md).
