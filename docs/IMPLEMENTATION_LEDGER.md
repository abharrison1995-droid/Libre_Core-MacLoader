# Implementation and evidence ledger

This is the concise tracked ledger. The full functional review and live readiness observations are in [Project status](PROJECT_STATUS.md); physical acceptance details are in the [T480s runbook](T480S_FIRST_INSTALL_RUNBOOK.md).

## Implemented in software

- Cross-platform/fixture hardware detection, sanitization, model matching, compatibility reports, and actionable BuildPlans.
- Shared CLI/TUI workflow for saved configuration drafts, exact target selection, policy/evidence review, preflight, and safe resume.
- Pinned dependency resolution/acquisition, SHA-256 integrity verification, archive checks, cache coordination, and offline reuse.
- Catalog-verified host tool installation and selection.
- Reviewed-profile EFI/config generation, machine-bound ACPI processing, private identity handling, structural validation, matching `ocvalidate`, and redacted manifests.
- Recovery discovery/acquisition contracts with bounded transport, signed chunklist verification, and artifact-bound persistence.
- Removable-device discovery, immutable non-destructive media plans, confirmation/re-enumeration checks, and guarded write/readback contracts.

The recent trust/lifecycle remediation addresses the recorded F01–F16 findings: machine-bound evidence validation, effective profile inputs, workflow-issued Recovery bindings, tool provenance, media artifact checks, protected identity/evidence paths, compare-and-swap configuration saves, cache/acquisition concurrency, cancellation, UI draft state, and detection uncertainty. Detailed mappings and regression-test names are retained in the ignored local `workspace/implementation-ledger.md` for this checkout.

## Remaining release evidence

| Gate | Current status |
|---|---|
| Exact Sequoia 15.0 / 24A335 Recovery | Externally blocked; product-to-build relationship is unproven. |
| Reference-machine ACPI and USB evidence | Must be freshly captured, private, and bound to the actual 20L8 / BIOS 1.62 configuration. |
| Real private SMBIOS identity | Created/reused only at the explicit private workflow checkpoint. |
| Physical USB write/readback | No production-qualified host adapter; writes remain unavailable. |
| Physical boot/install/acceptance | Not performed; no `SUPPORTED` status. |
| Windows/macOS host qualification | Not re-established in this review. |

## Verification provenance

The ignored local ledger records a prior result of **423 tests passed**, **79.11% branch-measured coverage**, clean mypy across 105 files, wheel/sdist builds, package smoke tests, compileall, and diff checks. These were not rerun for the 2026-10-02 documentation review. The observed local toolchain and blocked preflight are recorded separately in [Project status](PROJECT_STATUS.md).
