# P4 handoff — T480s EFI, trusted toolchain and private identity

Work in `/home/swarm/Desktop/LibreCore_MacLoader`. Read `START_HERE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/CONFIGURATION_WORKFLOW_PLAN.md`, `docs/DECISIONS.md`, `docs/T480S_REFERENCE_EVIDENCE_2026-09-10.md`, and the relevant G1/G2 research ledger entries before changing code. Preserve all existing user changes. Treat files under `workspace/private-t480s-evidence/` as private input: never commit, publish, quote unique identifiers from, or copy raw tables into tracked fixtures.

Implement P4 for the exact ThinkPad T480s 20L8 / BIOS N22ET85W 1.62 / Sequoia 15.0 build 24A335 candidate. This is an implementation and validation phase, not permission to claim physical support, erase media, install macOS, modify firmware, weaken gates, redistribute a prebuilt EFI, or expose SMBIOS identity values.

Required outcomes:

1. Reconcile the real sanitized evidence with the schema-driven configuration service. Add only sanitized, non-unique derived fixtures or records where necessary; provenance must distinguish observed hardware, private raw evidence and inference.
2. Pin and verify the approved ACPICA `iasl` tool and matching OpenCore 1.0.7 `ocvalidate` for each supported host used in qualification. Record URL, version, size, SHA-256, invocation, exit status and diagnostics. Caller-supplied binaries or flags must not self-qualify.
3. Import, validate, disassemble and review the private DSDT plus eleven SSDTs. Generate only machine/BIOS-bound reviewed ACPI sources required for this configuration. Compile them with the pinned `iasl`, retain diagnostics and digests, and reject stale/mismatched BIOS or evidence.
4. Replace remaining prototype EFI defaults with deterministic schema-driven transformations bound to the exact target, policy, profile, dependency lock, toolchain and evidence digests. Preserve the existing staging, archive, manifest, licence and validation safeguards.
5. Resolve the UHD 620 framebuffer and connector policy for the observed FHD non-touch panel using authoritative sources and explicit reviewed values. Do not inherit opaque values from third-party EFIs without independent justification.
6. Resolve Realtek ALC257 (`10EC:0257`, subsystem `17AA:2258`) policy using the bounded Sequoia layout candidates. Keep the choice experimental until physical speaker, headphone and microphone tests pass.
7. Model observed USB routes `SS01`, `SS02`, `SS03`, `HS06`, `HS07` and `HS08`. Keep USB-C logical correlation explicitly unresolved; do not fabricate a complete USB map. Ensure the first-install plan selects a positively identified USB-A route.
8. Keep Fibocom L830-EB WWAN disabled or unsupported unless a separately reviewed policy proves otherwise.
9. Implement qualified private SMBIOS identity generation/reuse. Store secrets outside tracked/public output with restrictive permissions. Tests must prove that raw and encoded identity canaries never enter logs, exports, reports, manifests or diagnostics. Do not generate a real identity during automated tests.
10. Run the matching real OpenCore 1.0.7 `ocvalidate` against the generated configuration. Structural-only validation must remain non-release; missing, mismatched, failed or timed-out validation must block promotion and media preparation.

Testing must include deterministic golden fragments with fake identities, stale evidence/BIOS/tool/catalog/profile invalidation, malformed ACPI/tool failures, USB-C unresolved-state preservation, exact file/plugin registration, redaction canaries, public API/CLI bypass attempts, package-data coverage, and existing regression/type/coverage gates. Use mocks for failure matrices, but record at least one real pinned `iasl` compilation and matching `ocvalidate` run separately from unit-test claims.

Do not begin Recovery acquisition, destructive USB writing or physical installation in P4. Finish with: files changed; exact commands and results; tool provenance; generated-output validation state; outstanding blockers; and an explicit statement of which G1/G2/P4 requirements remain open. Update the active plans and evidence ledger without marking T480s `SUPPORTED`.
