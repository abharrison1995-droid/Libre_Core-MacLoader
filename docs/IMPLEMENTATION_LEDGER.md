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

## Autoloader v1 implementation — P0

2026-10-03: hosted push and pull-request verification passed for `f566e0e9db23bcfeb42d1dcdd23385cc0e8b515f` (Actions runs 37127693099 and 37127696215). All Ubuntu/Windows × Python 3.11/3.14 functional and mypy cells and both package jobs passed. Coverage policy is recorded in [CI policy](CI_POLICY.md).

Repairs cover executable suffix preservation during quarantine, host-native synthetic tools, portable cancellation, explicit host guards in process cleanup and configuration locking, injected Linux discovery platform, and narrow POSIX-only assertions. Windows process-tree cleanup has explicit coverage. Local validation before the final audio regression: 474 passed, 79.04% branch coverage on Linux/Python 3.13.5; mypy clean for native and Windows stubs; wheel/sdist and both outside-checkout smoke checks passed.

The owner's layout-86 correction is preserved in commit `cbc0a05`, with a default-policy → BuildPlan → generated plist regression. Its hosted verification is pending at this entry. The two pre-existing owner documentation edits are still retained in the working tree. Physical testing remains prohibited by the master-plan readiness gate.

2026-10-03: P0 final audio regression commit `cbc0a050c651a72c8370b3e1dd5c44f02e330144` passed hosted Actions run 37127761753. This is the P0 known-green baseline including the owner's layout-86 correction.

P1 implementation adds `database/campaigns.py` and the single `t480s-20l8-n22et85w-162-sequoia` composition record. Linked policies/component matching rules are provenance-bound; drift and ambiguous selectors fail closed. Configuration policy lookup supports a referenced policy ID and profile search spans the existing profile registry. ACPI import and preflight use firmware candidate lookup. Component reconciliation and autoloader orchestration are still pending P2/P3.

P1 hosted verification: commit `7eb54b1` passed push/PR Actions runs 37128102659 / 37128104746, all required cells.

P2 software implementation adds observation projection/reconciliation, component and panel candidate matching, Linux EDID preferred timing, Windows WMI preferred timing and HDAUDIO codec/subsystem detection, plus optional evidence input scopes. Local checks: 501 passed, 79.31% branch coverage; native and Windows-platform mypy clean across 113 files. Missing touch proof stays unknown: neither EDID nor complete OS input inventory alone proves that touch hardware is absent. New source metadata contains no raw EDID, monitor instance identifier or serial. Physical evidence is still absent.

P3 in progress: private HMAC-bound sessions, automatic configuration create/resume, a shared NextAction engine, CAS session revisions and safe-action journal are implemented. Capture/build/Recovery/media handlers are subsequent phase work. Regression input `t480s_20l8_162_synthetic.json` is explicitly synthetic and cannot establish physical acceptance.
