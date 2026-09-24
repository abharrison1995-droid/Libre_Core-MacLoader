# Implementation plan: current working tree to a shippable v0.1.0

> **Status correction (2026-09-24):** The progress table, verification figures, and private-evidence/toolchain statements below are a 2026-09-11 historical snapshot. Current operator readiness is summarized in [README](../README.md) and [the T480s first-install runbook](T480S_FIRST_INSTALL_RUNBOOK.md). This checkout currently has no private ACPI capture or real SMBIOS identity; the Apple HTTPS Recovery endpoint returns 405 and exact 24A335 Recovery availability remains unproven; Linux removable-media code is implemented and disposable-image-tested but not physically qualified. The configured user cache now has a verified pinned toolchain. No physical USB, BIOS, internal disk, or macOS installation operation was performed in this implementation pass.

> Next configuration-workflow phase: follow [CONFIGURATION_WORKFLOW_PLAN.md](CONFIGURATION_WORKFLOW_PLAN.md), dated 2026-09-09, and accepted ADR-007. It records the user's approved defaults, phased implementation, files, tests and human evidence requirements. It supplements this release plan; it does not close or weaken G0–G7. Historical baseline/status text below must be reconciled with current source and fresh evidence before implementation.

Updated: 2026-09-11, after the P0-P6 configuration-workflow implementation and exact T480s candidate EFI qualification. The current checkout is **not a release candidate**. Sequoia first remains the agreed scope; the pre-slice baseline and historical review findings are retained below for traceability.

Evidence update 2026-09-10: real sanitized T480s 20L8 inventory, BIOS observations, private ACPI tables, ALC257 identity and partial USB topology are available as summarized in [T480S_REFERENCE_EVIDENCE_2026-09-10.md](T480S_REFERENCE_EVIDENCE_2026-09-10.md). P4 software qualification is complete for the exact Sequoia 15.0 build 24A335 candidate on the current Linux x86_64 host. Type-C logical mapping, Recovery/media, installation and physical acceptance remain open.

Detailed continuation: execute P5–P8 and their mandatory three-agent Luna review gates from [REMAINING_PHASES_EXECUTION_PLAN.md](REMAINING_PHASES_EXECUTION_PLAN.md). It defines phase evidence, safety/human checkpoints, review roles, repair loops and the release decision without weakening G0–G7.

This is the active execution plan. It supersedes earlier statements that G0 is closed and that the builder/recovery/media modules are wholly absent or complete. The engineering specification remains the product contract. Historical review resolutions remain in [IMPLEMENTATION_REVIEW.md](IMPLEMENTATION_REVIEW.md); the open issue register below governs current work.

### Current progress and evidence

| Area | Implemented progress | Remaining qualification / status |
|---|---|---|
| Detection and compatibility | Stricter model/component matching, uncertainty propagation, Windows inventory expansion, Linux fixes, input/Bluetooth capabilities | Substantial progress; malformed CIM, sysfs failures, raw evidence and CPU parity remain open (S08-S10, S14) |
| Plans and dependency contracts | Canonical plan/catalog/policy digests, exact resolver-set binding in acquisition and EFI publication, component/toolchain/identity manifest binding, strict serialized readiness checks; schema-driven configuration contracts bind exact target, stable model, hardware/evidence digests and reviewed profiles into BuildPlans | P4-scoped G1/G2 software qualification is verified; Recovery, media and physical release validation remain open |
| Downloader/cache/archive | Streaming dependency hashes, limits, safe paths, atomic file/index publication, ownership-safe lock protocol with liveness checks and atomic reclaim | S06 lock ownership safety closed with 9 focused tests; concurrent miss handling and archive redundancy remain open (S07, S11) |
| EFI builder and CLI | `macloader/build/efi.py`, schema-driven P4 profile generation, machine-bound ACPI processing, ordered kexts, manifests, plan-bound extraction, structural/config validation, real matching ocvalidate and private identity reference | Hosted clean-checkout/package evidence passes; exact Recovery, media and physical acceptance remain pending |
| Recovery | Exact-target contracts, Apple policy/catalog discovery, redacted CLI/service resolution, signed chunklist verification, bounded resumable acquisition and rollback-safe publication | Local failure matrix and one real non-destructive discovery are verified; Apple returned product `696-28424` without exact `24A335`, HTTPS discovery returned 405, no payload was downloaded, and the exact Recovery gate is externally blocked |
| Removable media | Immutable plans, Windows-first whole-disk discovery boundary, typed confirmation, re-enumeration, bounded disposable-image writer and complete EFI/Recovery readback | P7 software and hosted package qualification pass; no sacrificial USB write/readback or Linux advertisement has occurred (G5) |
| CI and packaging | Windows/Linux Python 3.11/3.14 workflow, wheel/sdist smoke, branch coverage and portable mypy checks are green in hosted run `34644459857` | Local sdist generation remains unavailable because the host's `build` module cannot run as a module; physical media and exact Recovery evidence remain open (S01, S13) |
| Workflow and hardware | Shared `WorkflowService` drives CLI and the workflow-scoped Textual TUI for configuration, evidence, review, dependencies, EFI, Recovery and non-destructive media planning; semantic parity, redacted export and safe resume are tested | Legacy standalone CLI utilities are intentionally not claimed as separate TUI screens; BIOS/installation guidance and physical acceptance remain open (G6-G7) |

Fresh P5-P7 verification on this Linux/Python 3.13 environment: `python3 -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79` = **373 passed, 81.00% branch coverage**; `python3 -m mypy macloader tests --follow-imports=skip` = **clean across 104 source files**; `python3 -m compileall -q macloader tests` and `git diff --check` completed cleanly. The default mypy invocation remains externally blocked by the installed NumPy stub's Python-version syntax before source checking. Hosted run `34644459857` passed Ubuntu/Windows Python 3.11/3.14 tests at **80.88%, 80.34%, 80.18% and 79.61%** respectively, with hosted mypy clean across 104 files; both hosted wheel and sdist smoke jobs passed. The exact P4 configuration/build gate accepted Sequoia 15.0/24A335, and the final real `ocvalidate` run returned `VALID` / exit 0. These results establish software/toolchain and hosted package qualification, not exact Recovery availability, sacrificial USB or physical acceptance.

Hosted CI run `34644459857` provides the required clean-checkout Windows/Linux Python 3.11/3.14 test and mypy evidence plus wheel/sdist smoke. A local wheel was also installed into an isolated target outside the checkout; its CLI/TUI semantic parity smoke passed. The local host lacks the `build` module, so local sdist generation was not claimed. No exact Recovery payload, physical USB write, BIOS change, disk installation or physical acceptance was performed; those gates remain open.

## Gate status

| Gate | Current state | What closes it |
|---|---|---|
| G0 foundation | **Reopened / mostly implemented** | S01-S02 and S06-S10 local repairs are implemented; clean-checkout verification and later validation gates remain |
| G1 policy/toolchain | Configuration contracts and candidate T480s/Sequoia policy/release records now exist; live machine/tool evidence remains pending | Frozen T480s/Sequoia policy, verified upstream artifacts and host tools |
| G2 T480s EFI | Prototype exists; **not valid for release** | S02-S04/S11 fixed; complete generated config/ACPI/identity and actual matching ocvalidate pass |
| G3 T480 variants | Detection fixtures exist; generated variants unqualified | Model-specific builds and negative validation matrix |
| G4 Recovery | Preliminary helper | S05/S12 plus official acquisition, CLI integration and real verified asset |
| G5 USB | P7 software/hosted package qualification passes; physical qualification pending | S12 plus sacrificial-media write/readback and boot evidence |
| G6 workflow/distribution | Shared CLI/TUI workflow and hosted wheel/sdist qualification pass | Guidance, exact Recovery, destructive media and physical acceptance remain |
| G7 physical release | Pending | Real T480s full acceptance and reproducible release evidence |

A green local suite does not close a gate whose required behavior lacks tests. Prototype code counts as implementation progress, but not as a satisfied product requirement.

## Release scope and completion contract

User-confirmed baseline: **Sequoia first** (2026-09-08), with T480s as the first reference machine under the engineering specification. Freeze the exact OS release/build and hardware configuration in G1. Sonoma and Tahoe remain separate policies and require their own evidence; their presence in a YAML file does not qualify them for release.

Ship a generated EFI workflow for both T480s and T480. T480 may ship explicitly EXPERIMENTAL without physical acceptance, as allowed by the specification. A configuration becomes SUPPORTED only after the full recorded acceptance suite. Unqualified optional variants must be visibly restricted rather than silently receiving the baseline policy.

v0.1.0 requires a fresh installation of MacLoader to guide a user through inventory, compatibility, a concrete plan, verified acquisition, EFI generation, validation, Apple Recovery acquisition, guarded USB preparation, BIOS guidance, installation, and booting installed macOS on the reference T480s. No manual EFI assembly may be necessary. All required acceptance checks must pass with reproducible evidence.

Keep the specification's boundaries: no firmware modification, prebuilt EFI redistribution, automatic root patching, silent internal-disk writes or shared SMBIOS identities. A Tahoe workaround is a researched policy decision, not an implied commitment to add root patching in v0.0.5.

## Open shipping issue register

All S-items are open unless explicitly labeled documentation-complete. Each item has a responsible workstream, dependency and proof of completion. “Before ship” means required for the advertised v0.1.0 scope; optional experimental targets do not bypass the shared safety gates.

### Immediate blockers: packaging and EFI trust

| ID / priority / owner | Required change | Acceptance evidence and dependency |
|---|---|---|
| [/] S01 — In Progress; packaging | Scope `.gitignore` build-output rules to avoid ignoring `macloader/build/`; include builder sources and required new modules/resources in the reviewed commit. Check all intended source files, not only efi.py. A local wheel built from ignored working files is insufficient evidence. | A local wheel and isolated clean-target install smoke pass for builder, Recovery, removable and workflow modules, including CLI/TUI semantic parity. Hosted run `34644459857` passes Windows/Linux wheel/sdist smoke; local sdist generation remains unavailable because the `build` module is not installed. |
| [/] S02 — In Progress; contracts/EFI | Bind a build to canonical BuildPlan digest, validated catalog/policy identity, exact selected artifact lock, component selections and toolchain. Recompute readiness from validated fields rather than trusting caller-supplied booleans. Apply the same checks in the public builder and CLI/orchestrator; define permitted historical locked builds explicitly. Frozen dataclasses with mutable lists are not sufficient immutability. | Local builder checks reject different plans, stale catalogs, modified artifacts, missing/extra paths and mismatched resolver sets before extraction. Artifact locks now include archive type, URL basenames are exact, duplicate archive members are rejected, license notice hashes are bound into the manifest, and published manifests are compared to the validated build identity. Remaining: immutable accepted inputs and the qualified G1 toolchain record. Depends on S01; finalize policy identities in G1. |
| [/] S03 — In Progress; validation | Structural checks must not return release `VALID`. Define structural-only versus fully validated states. Run qualified matching OpenCore ocvalidate, preserve version/exit status/diagnostics, and verify configuration entries and files. Refuse promotion/USB use on missing, mismatched, failed or timed-out validation. | Structural-only output remains non-release; qualified validation now requires a caller-supplied expected manifest bound to the validated inputs; timeout/nonzero/missing/integrity-failed validators are rejected; manifest mutation and absolute/symlink config paths are covered. CLI-supplied validators remain pending rather than self-qualifying. Remaining: trusted matching ocvalidate invocation and G1 toolchain evidence. Depends on S01-S02 and qualified G1 toolchain. |
| [/] S04 — In Progress; identity/output | Construct the returned identity reference against the final published directory, not the renamed temporary stage. Use qualified local identity tooling/policy, private storage and deliberate rebuild reuse; random token strings and a fixed SMBIOS model are prototype behavior. | Identity storage cleanup is initialized safely, private references expose only a logical filename, and the published manifest binds the identity digest. Remaining: qualified identity tooling/policy and complete redaction evidence in G1-G2. Depends on S01-S02. |

### Reliability and safety repairs

| ID / priority / owner | Required change | Acceptance evidence and dependency |
|---|---|---|
| [x] S05 — Completed; Recovery | Replace `hashlib.sha256(part.read_bytes())` with bounded streaming hash; enforce a monotonic total deadline, cancellation, declared/max size and disk-space policy. Use unique owned temporary files, normalized failures and a verified Apple redirect/source policy; share bounded transport primitives without assuming dependency GitHub hosts apply to Recovery. | A large synthetic asset uses bounded memory; oversized, truncated, slow-trickle, timeout, cancel, hash-mismatch and disallowed-redirect cases fail cleanly, retaining any prior valid destination. Tests use fake streams/clocks, not a 16-GiB allocation. Required before exposing Recovery download to users; G4. Verified by `tests/unit/test_recovery_acquisition.py`. |
| [x] S06 — Completed; cache | Replace age-only stale-lock stealing with an ownership-safe design (OS lock or proven ownership/liveness protocol). A long-running live owner must not lose its lock after five minutes; a former owner cannot release a replacement owner's lock. | Deterministic interleavings test live locks past the old threshold, owner death, contention timeout and replaced ownership. Cleanup removes only the current owner's lock. No sleep-based five-minute test. Reopens G0.6. Verified by `tests/unit/test_cache_locking.py`. |
| [x] S07 — Completed; acquisition | Coordinate the complete check/download/verify/publish lifecycle per artifact. Recheck the cache after acquiring ownership; protect active temporary files from cache initialization, cleanup, pruning and clearing. Define waiting/cancellation semantics. | Two concurrent misses for the same lock yield one successful acquisition and valid results for both callers; different artifacts can progress safely. Failed/cancelled owner can be retried without deleting another owner's work. Exercise processes, not just an in-process mock. Depends on S06; G0.6. Verified by `tests/unit/test_acquisition_concurrency.py`. |
| [x] S08 — Completed; Windows detection | Separate query execution success, JSON/schema parse success and confirmed-empty inventory. Malformed/non-row data must remain unknown, not complete. | Malformed JSON, scalars, mixed rows, failed queries and missing required fields cannot upgrade inventory; successful documented empty output is distinct. Regression tests assert resulting compatibility/build eligibility, not only parser output. Reopens G0.4. Verified by `tests/unit/test_windows_provider.py`. |
| [x] S09 — Completed; Linux detection | Handle denied/disappearing sysfs directories, broken links and enumeration failures with category-specific uncertainty or a domain error. Do not convert failures into confirmed absence. | Simulated PermissionError/FileNotFoundError/OSError during enumeration yields controlled CLI stderr/exit behavior or a partial uncertain snapshot; no traceback or actionable false completeness. Reopens G0.4/G0.7. Verified by `tests/unit/test_linux_provider.py`. |
| [x] S10 — Completed; hardware contracts | Validate/normalize `raw_evidence` and nested `inventory_status` at snapshot boundaries; compatibility must not call `.get()` on arbitrary imported types. | null/list/string/wrong-shaped evidence and malformed nested status produce a contextual domain error or UNKNOWN policy, never an unhandled crash or inferred completion. Check direct API and JSON fixture CLI paths. Reopens G0.3/G0.7. Verified by `tests/unit/test_contracts.py`. |
| [x] S11 — Completed; EFI/archive efficiency | Consolidate redundant snapshots/extraction/copies while retaining one trusted private artifact view, bounded extraction and collision protection. Preserve selected bundles/plugin topology; never remove integrity checks merely to reduce I/O. Documented peak disk budget: 256 MiB per artifact (`DEFAULT_MAX_ARTIFACT_EXPANDED_BYTES`), 512 MiB total build peak disk budget (`DEFAULT_MAX_BUILD_EXPANDED_BYTES`), 10,000 member limit (`DEFAULT_MAX_ARCHIVE_MEMBERS`). | Counted and measured copies (exactly 1 private snapshot per dependency, 0 intermediate extraction copies), verified topology preservation for complex bundles and nested plugins (e.g. VirtualSMC and VoodooPS2), atomic collision protection, and adversarial security tests. Verified by `tests/unit/test_efi_archive_efficiency.py`. |
| [x] S12 — Completed; Recovery/USB guards | Add automated coverage of Recovery and removable-media guards before wiring them into a destructive workflow. Current injected callback is not a complete USB implementation. | For USB: no write callback on system/non-removable/mounted/undersized/ambiguous/stale targets, wrong confirmation, bad source or failed EFI/Recovery validation; re-enumeration catches hot-swap/capacity/identity changes. Test interruption and readback failure. Recovery matrix is S05 plus product/version/integrity checks. Implement/test host adapters with disposable images before sacrificial USB; G4-G5. **CHECKPOINT (2026-09-08):** Implementation and two Luna Medium reviews complete; all 250 tests passing (33 S12 guard tests + 217 existing). mypy clean across 72 files. Review findings fixed: empty sources, mandatory readback, stable device identity, interrupt normalization, matrix enforcement, symlink boundary, complete disposable readback, and integrity metadata validation. Files modified: `macloader/removable/writer.py`, `macloader/removable/__init__.py`, `macloader/recovery/acquirer.py`, `macloader/recovery/__init__.py`, `tests/unit/test_removable_recovery_guards.py`, `tests/unit/test_recovery_acquirer.py`. |

### Remaining integration and maintenance work

| ID / priority / owner | Required change | Acceptance evidence and dependency |
|---|---|---|
| [/] S13 — In Progress; release evidence required; CI/docs | Keep README/START_HERE/handoff current; developer setup must install `.[dev]`. CI should collect branch coverage and publish results; establish measured thresholds for critical modules after adding negative tests rather than inventing a current percentage. | Local candidate: 373 passed, 81.00% branch coverage, mypy clean across 104 files. Hosted run `34644459857` passes the four Windows/Linux Python 3.11/3.14 coverage gates and both wheel/sdist smoke jobs. Exact Recovery, sacrificial USB and physical evidence remain open. Critical build/cache/recovery/USB failure cases remain tested regardless of aggregate percentage. G0/G6. |
| [x] S14 — Completed; detection/policy | Resolve prior open Windows/Linux CPU-generation mismatch, duplicate warnings/unresolved entries and schema/cache version-character disagreement. Keep legitimately empty inventory driven by evidence rather than a hardcoded model assumption. | Paired host fixtures classify the same supported CPU consistently; stable deduplicated reports preserve all distinct reasons; every schema-accepted artifact identifier/version can produce a safe cache path. G0.2/G0.4-G0.6. Verified by `tests/unit/test_detection_policy_residual.py`. |
| [x] S15 — Completed; maintainability | Remove unused imports and consolidate duplicate canonical digest routines without changing the contract accidentally. | Type/tests clean; golden digest/round-trip checks remain stable or schema migration is explicit. Do after S02 defines the canonical input. Before G6 release freeze. Verified by `tests/unit/test_maintainability_digest.py`. |

The user-supplied Terra review establishes the S01-S13 findings. Source inspection in this update confirms the named paths/mechanisms; no new agent swarm was run. S14-S15 also retain unresolved items from the previous diff review so they are not lost. S02 additionally captures the earlier stored `build_ready` defect. Fixes require focused regression evidence, not just another green run of the original 79 tests.

## G0 — Reopened foundation completion checklist

Owner roles: core, detection, acquisition and packaging. Existing implementations are retained; only unfinished behavior/evidence is reopened.

- [ ] G0.1: preserve the 79-test/mypy baseline; fix S01, run clean-checkout Windows/Linux and wheel checks, collect meaningful coverage (S13).
- [x] G0.2 core implementation: exact normalized model/component matching and removal of broad defaults are present; historical tests pass. Remaining cross-host/report consistency is explicitly S14, not a claim of full hardware acceptance.
- [ ] G0.3: existing actionability and policy propagation remain; close S02/S10 readiness/import boundary cases.
- [ ] G0.4: existing inventory expansion and input/Bluetooth propagation remain; close S08-S09/S14 with end-to-end uncertainty regressions.
- [ ] G0.5: existing catalog digest, strict variants and per-directory registry remain; complete S02's builder boundary and canonical contract tests.
- [ ] G0.6: existing bounded dependency downloader and atomic publication remain; close S06-S07/S14 and preserve archive safety for S11.
- [ ] G0.7: existing lazy initialization/user workspace remain; close S09-S10 controlled error paths and verify fresh installed CLI behavior.
- [ ] G0.8: database numbering/default improvements remain; this pass updates entry-point docs, but finish S13's full command/support documentation audit at release.

Exit gate: S01 and all applicable foundation items above are fixed with regression evidence; local and hosted supported-host checks pass from tracked sources. “Locally green” and “G0 closed” are separate claims. G1 research can proceed independently while repairs run, but no later gate may waive these defects. G0 closes the generic contract/rejection mechanisms in S02; G1 qualifies the actual policy/toolchain inputs and G2 proves their real build behavior, so G0 does not depend on a finished EFI.

### G1 — Freeze one buildable policy and its toolchain (v0.0.5 prerequisite)

Depends on: G0. Owner roles: platform policy / hardware maintainer.

Status: **P4-scoped G1 freeze verified; G1 overall remains open for Recovery and release-host coverage.** The exact 20L8/BIOS/Sequoia candidate is bound to the reviewed profile, locked dependency catalog, private ACPI evidence digest and unresolved USB-C policy. The trusted Linux x86_64 record pins OpenCore 1.0.7, ACPICA iASL 20260408 and OpenCore macserial 2.1.8; all tool bytes and version banners were checked from the controlled ignored workspace. Ten release dependencies were acquired and replayed offline from the lock. Research Items 011–012 record the authoritative graphics/audio policy and tool/build evidence.

- [x] Capture a sanitized live T480s snapshot, BIOS settings/version, display/input/storage/WLAN variant and the exact OS target. The exact P4 candidate uses private ACPI evidence and partial USB evidence; Type-C logical correlation remains explicitly unresolved.
- [x] Audit and acquire the selected release set through the pinned resolver/cache, including archive layout, licenses and SHA-256 checks. The final build was replayed with `offline=True`; hosted clean-checkout/package coverage is recorded in run `34644459857`.
- [x] Pin matching OpenCore Sample.plist, ocvalidate, local identity tooling and ACPI compiler for Linux x86_64. Recovery tooling and additional host records remain open.
- [x] Specify the P4-scoped SMBIOS, UHD 620 graphics, ALC257 audio, input, network, WWAN omission and USB policy. The USB policy deliberately selects SS01 as the first-install route without fabricating a complete map; physical audio/graphics acceptance remains open.
- [x] Introduce and exercise typed/versioned BuildPlan, artifact lock, ACPI/config, identity reference, build manifest and validation report bindings. Legacy preliminary-plan readability is retained.
- [ ] Resolve Tahoe audio/Wi-Fi/SMBIOS/storage questions before admitting a Tahoe build to qualification. Reassess PM981 risk from upstream evidence; selecting NVMeFix alone is not proof that storage is safe for installation.

Exit gate: one exact T480s/OS combination has no unresolved boot-critical policy; every selected artifact/tool is verified and every planned output has an unambiguous source and destination. Any remaining optional limitation has an explicit release policy and acceptance criterion.

### G2 — Generate and validate T480s EFI (v0.0.5 + early v0.0.7)

Depends on: G1. Owner role: EFI / ACPI implementation.

Status: **P4 exact-candidate software qualification verified; G2 overall remains open for physical acceptance and unresolved USB-C correlation.** The build uses the pinned OpenCore 1.0.7 schema, dependency-aware kext ordering, reviewed graphics/audio policy, private identity lifecycle, machine-bound ACPI processing and the real matching Linux `ocvalidate`.

- [x] Build in a fresh staging directory from the locked set. Actual release members map to EFI/BOOT and EFI/OC; selected components and pinned notices are preserved, with collision and archive safety checks.
- [x] Generate config.plist from the pinned schema with typed values, dependency-aware kext/plugin ordering, driver/ACPI entries, UHD 620 and ALC257 properties, NVRAM policy and exact-target bindings.
- [x] Generate/compile the machine-bound ACPI additions. The private capture is disassembled and retained only in ignored diagnostics; 12 OEM SSDTs compile, the OEM DSDT remains diagnostic-only, and three reviewed AML additions are emitted. No complete USB map is emitted.
- [x] Use the private identity lifecycle with a deterministic fake identity for this automated qualification run, protected local storage/reuse and redacted manifest/reference handling. Real identity generation remains an explicit human/private choice.
- [x] Run structural/config/dependency/identity checks and the matching real OpenCore 1.0.7 `ocvalidate`; the final candidate returned `VALID` with exit 0.
- [x] Publish the generated EFI only after validation and retain sanitized manifest/diagnostic metadata. The output contains no raw private ACPI tables or private evidence path text.

Exit gate: fixture-to-EFI integration passes with actual matching ocvalidate on supported hosts; negative fixtures prove invalid output is rejected. Rebuilding from the same lock/policy/fake identity produces equivalent EFI bytes. Real reference identity remains private. VALID requires all configured structural, dependency, identity and real matching ocvalidate checks to pass against the published output. It still does not mean physically accepted.

### G3 — Add T480 variants and complete validation (v0.0.6-v0.0.7)

Depends on: G2. Owner role: model policy / validation.

- [ ] Add T480-specific ACPI, power/battery, display/input and USB policies using the shared builder; cover iGPU-only and MX150 fixtures, alternate WLAN, missing inventory and risky storage.
- [ ] Apply dGPU disable policy only to positively identified matching hardware and verify its ACPI target. Test that it is absent from iGPU-only output. Power-off behavior requires physical evidence.
- [ ] Finish the validator matrix: dangling/duplicate entries, absent plugin parents, bad executable paths, schema/version drift, unresolved requirements, identity errors, compiler/validator timeouts and mutated artifacts.
- [ ] Separate experimental-build acknowledgement from validation success and hardware support status. Produce BIOS guidance from the same policy data.

Exit gate: all admitted T480/T480s fixture combinations pass real matching ocvalidate; rejected combinations fail before output publication. Neither machine is promoted to SUPPORTED by automated tests.

### G4 — Acquire usable Apple Recovery assets (v0.0.8)

Depends on: G1 toolchain and G2 manifest/validation contracts; can be implemented alongside G3.

Progress: an asset/transport helper exists; S05 and S12 regression coverage is complete. The pinned OpenCore `macrecovery.py` backend and its signed chunklist verification behavior are recorded in Research Item 010, but official target discovery, Recovery CLI integration and live verified acquisition remain open.

P5/R5 update 2026-09-11: exact-target discovery contracts, HTTPS Apple policy/catalog, redacted CLI/service resolution, signed chunklist verification and rollback-safe bundle publication are implemented and locally verified. A real non-destructive query through the pinned OpenCore protocol returned product `696-28424` without an exact build, so it is recorded as `AMBIGUOUS`; MacLoader did not lock or download it. HTTPS discovery returned HTTP 405, and no HTTP response is accepted as authoritative. The exact `15.0/24A335` Recovery exit gate is externally blocked; do not change the target without user approval. R5 completed with three independent Luna reviewers and repaired acquisition, path, cancellation, and evidence-boundary findings; no unresolved review blocker remains.

- [ ] Implement recovery listing/download through the pinned OpenCore `macrecovery.py` backend and Apple's infrastructure; resolve the requested target explicitly and record the returned product/build. Never silently fall back to another OS.
- [ ] Verify the image using the applicable upstream integrity/chunklist mechanism, record hashes/provenance and implement bounded retries, cancellation, disk-space checks and interrupted-download cleanup. Reuse verified local assets offline where supported.
- [ ] Bind recovery metadata and minimum size/network requirements to the selected EFI/OS policy. Explain required network access, including the chosen Ethernet/Wi-Fi route during Recovery.

Exit gate: mocked failure paths pass and an opt-in real download produces a verified, identified Recovery asset compatible with the qualification target. Actual boot is checked in G5/G7.

### G5 — Build guarded removable installation media (v0.0.9)

Depends on: G3 and G4. Owner role: host storage adapters / safety tests.

Progress: P7 software guards and a Windows-first discovery/backend boundary are implemented. The shared writer refuses unbound plans, requires opaque stable identity/re-enumeration, typed expiring confirmation, bounded no-follow source snapshots and complete EFI/Recovery readback. Linux is explicitly disabled until separately advertised. Hosted run `34644459857` passes the Windows/Linux Python 3.11/3.14 matrix, mypy and wheel/sdist smoke; the Windows backend remains unqualified for physical writes.

R7 update 2026-09-11: three independent Luna reviewers found and the implementation repaired fail-open mounted-volume detection, missing post-write invalidation, and raw serial-derived stale-target diagnostics. The repeated swarm and originating security recheck reported no remaining findings; the workflow reviewer’s localized recheck resolved the hosted-evidence documentation finding after run `34644459857` passed. Sacrificial USB and physical boot evidence remain open.

- [ ] Implement separate Windows/Linux removable-device discovery and write adapters behind one contract. Enumerate whole-device identity, model, serial where available, exact capacity, partitions and system-disk relationship. Refuse internal/system/ambiguous devices.
- [ ] Provide a dry-run plan covering partitions/filesystems, EFI and Recovery placement, required capacity and data destruction. Require typed confirmation tied to the exact target. Re-enumerate identity/capacity/system role immediately before each destructive stage; hot-swap or renumbering invalidates confirmation.
- [ ] Gate writes on successful EFI validation, verified Recovery and adequate privileges. Do not silently elevate. Refuse unexpected mounted/busy targets; preserve existing EFI via a documented backup/confirmation policy where applicable.
- [ ] Handle cancellation, unplugging, disk-full and partial writes with explicit failure state. Verify files and hashes after writing, flush and safely unmount/eject; never label an incomplete device ready.
- [ ] Test discovery/safety/write sequencing using mocks and disposable disk images first, then explicitly selected sacrificial USB media on each supported host. Record the tested partition layout and firmware boot behavior.

Exit gate: negative safety tests prove no destructive call occurs for an unsafe/stale/unconfirmed target. A verified USB created on each advertised host boots the reference machine into Recovery. No internal disk is automatically modified.

### G6 — Finish the user workflow and distribution (v0.1.0 preparation)

Depends on: stable G2-G5 service contracts. Owner role: UX / packaging.

- [/] Implement the specification's Textual TUI over the same services as the CLI for the P6 schema-driven workflow: configuration, evidence, compatibility/review, target/options, dependencies, EFI, Recovery, USB status/media preflight and completion guidance. Keep cancellation and experimental/blocked states explicit. Legacy standalone CLI utilities are not claimed as separate TUI screens; both clients produce the same semantic configuration, issue and plan/lock results for the shared workflow.
- [ ] Add model/OS BIOS checklist, installation steps, booting installed macOS, safe EFI transfer guidance with backup/explicit target confirmation, limitations and troubleshooting. Installation remains a guided user action; do not silently write the internal EFI partition.
- [ ] Package a wheel/sdist with all policies/templates/resources, declared dev tooling and third-party notices. Resolve the project's own license with the maintainer. Test installation on clean Windows/Linux environments and configured workspaces; document host prerequisites and unsupported hosts.
- [ ] Finish structured redacted logs and a shareable diagnostic bundle; verify secrets cannot leak through nested evidence or errors. Define exit codes, JSON schema versions and recovery from interrupted operations.

Exit gate: a user can complete the non-destructive P6 workflow from a clean install with no source checkout or hand-edited EFI. CLI/TUI share validation and safety rules, local wheel/clean-target package smoke tests pass, and docs match actual commands. Hosted Windows/Linux wheel/sdist evidence, exact Recovery, destructive media and physical acceptance remain required for later gates.

### G7 — Physical acceptance and release (v0.1.0)

Depends on: G0-G6. Owner roles: hardware tester / release maintainer.

- [ ] Execute [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md) on the reference T480s: picker, Recovery, installer, installation, installed-system boot, graphics/display/brightness, all input, battery, audio/mic, Ethernet/Wi-Fi/Bluetooth, USB, webcam, sleep/wake/restart/shutdown and fitted optional devices.
- [ ] Record exact hardware/BIOS, OS build, git revision, policy/lock and build hashes, sanitized logs, per-check outcome and tester/date. Repeat relevant checks after any policy or artifact change. Failed required checks reopen the owning milestone.
- [ ] Run equivalent acceptance on real T480 variants if claiming them SUPPORTED. Otherwise ship only an explicit EXPERIMENTAL status and document missing evidence. Do not extrapolate one accepted variant across OS releases or different hardware.
- [ ] Complete clean-host end-to-end rehearsal, checksum/manifest publication, licenses/notices, release notes and known limitations. Update support matrix and package version only from recorded evidence; retain reproducible release inputs.

Exit gate: the release completion contract above is demonstrably met. Remaining experimental configurations are accurately labeled and cannot masquerade as the accepted baseline.

## Execution order from today

1. **Repair batch A — reproducible source and honest validation:** S01 first, then S02/S03 immediate rejection/status safeguards and S04 final identity reference. Do not add more phase functionality before the packaging and EFI trust boundaries are secured.
2. **Repair batch B — reliable foundation:** S06 then S07; S08-S10 and S14. Add focused tests as each defect is fixed. Close the reopened G0 evidence gate with S13's clean-checkout/CI checks.
3. **Qualification batch C — one real target:** finish G1 for T480s/Sequoia, including actual artifact layouts and host tools. Then finish G2's schema-based config, ACPI, USB map, identity and matching ocvalidate; S11 optimization follows correctness. Contracts already present are inputs to refine, not work to discard.
4. **Integration batch D — variants and installer:** G3; in parallel where prerequisites allow, S05/S12 and G4. Only then G5 real adapters with fresh device identity checks, image-based tests and explicitly selected sacrificial media.
5. **Release batch E — complete product:** G6 including S13/S15, then G7 physical acceptance and release packaging. A local test pass cannot replace either gate.

Critical path: **S01 -> EFI trust repairs + G0 reliability -> G1 -> G2 -> G3/G4 -> G5 -> G6 -> G7**. Platform research and documentation may progress alongside independent repairs. Full S03 real-validator qualification depends on G1; the immediate repair must still prevent structural-only output being called VALID.

## Shipping checklist and evidence ledger

- [ ] Every S01-S12 defect is closed with linked regression evidence; residual S14 contract/detection bugs are closed. No untested Recovery/USB safety path is exposed.
- [ ] Required source, templates, policy data and resources are present in the release commit and its wheel/sdist; S13/S15 release work is complete.
- [ ] G0-G6 exit gates pass on the exact candidate revision; supported host/Python CI and installed-package tests are green.
- [ ] Real matching ocvalidate passes every release build; the report binds to output hashes, selected policy/tool versions and the private identity reference without exposing identity values.
- [ ] Exact T480s/Sequoia hardware/BIOS/OS build passes G7 using generated EFI and verified installer media. Support matrix distinguishes accepted and experimental variants.
- [ ] Reproduction inputs, recovery provenance, license/notices, guidance, known limitations and release artifacts are retained and reviewed.

For each S-item and G-task record **status, owner, commit, regression test, host/Python, artifact/report link and remaining evidence**. Allowed statuses: open, implemented awaiting verification, verified closed, externally blocked (name missing evidence). A checked box means the stated acceptance proof exists. Reopen it when new evidence contradicts the guarantee.

Hardware access and live upstream/tool qualification are explicit external dependencies. No calendar estimate or completion percentage is assigned until G1 bounds those risks. If an optional configuration fails, keep it experimental; if the reference configuration fails a required check, v0.1.0 is not ready to ship.
