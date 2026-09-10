# Implementation plan: current working tree to a shippable v0.1.0

> Next configuration-workflow phase: follow [CONFIGURATION_WORKFLOW_PLAN.md](CONFIGURATION_WORKFLOW_PLAN.md), dated 2026-09-09, and accepted ADR-007. It records the user's approved defaults, phased implementation, files, tests and human evidence requirements. It supplements this release plan; it does not close or weaken G0–G7. Historical baseline/status text below must be reconciled with current source and fresh evidence before implementation.

Updated: 2026-09-10, after the P0-P3 configuration-workflow implementation slice and real T480s evidence capture. The current checkout is **not a release candidate**. Sequoia first remains the agreed scope; the pre-slice baseline and historical review findings are retained below for traceability.

Evidence update 2026-09-10: real sanitized T480s 20L8 inventory, BIOS observations, private ACPI tables, ALC257 identity and partial USB topology are available as summarized in [T480S_REFERENCE_EVIDENCE_2026-09-10.md](T480S_REFERENCE_EVIDENCE_2026-09-10.md). P4 may begin against that exact evidence. Type-C logical mapping, trusted `iasl`/`ocvalidate`, generated EFI qualification and all installation/physical acceptance remain open.

This is the active execution plan. It supersedes earlier statements that G0 is closed and that the builder/recovery/media modules are wholly absent or complete. The engineering specification remains the product contract. Historical review resolutions remain in [IMPLEMENTATION_REVIEW.md](IMPLEMENTATION_REVIEW.md); the open issue register below governs current work.

### Current progress and evidence

| Area | Implemented progress | Remaining qualification / status |
|---|---|---|
| Detection and compatibility | Stricter model/component matching, uncertainty propagation, Windows inventory expansion, Linux fixes, input/Bluetooth capabilities | Substantial progress; malformed CIM, sysfs failures, raw evidence and CPU parity remain open (S08-S10, S14) |
| Plans and dependency contracts | Canonical plan/catalog/policy digests, exact resolver-set binding in acquisition and EFI publication, component/toolchain/identity manifest binding, strict serialized readiness checks; schema-driven configuration contracts now bind exact target, stable model, hardware/evidence digests and reviewed profiles into preliminary BuildPlans | Real ocvalidate qualification, trusted toolchain and release validation remain open (S03-S04, G1-G2) |
| Downloader/cache/archive | Streaming dependency hashes, limits, safe paths, atomic file/index publication, ownership-safe lock protocol with liveness checks and atomic reclaim | S06 lock ownership safety closed with 9 focused tests; concurrent miss handling and archive redundancy remain open (S07, S11) |
| EFI builder and CLI | `macloader/build/efi.py`, `build`/`validate`, staging, manifests, plan-bound extraction, structural/config validation, qualified-validator contract and private identity reference; builder unit tests; candidate source included | Hosted clean-checkout evidence and real G1/G2 ocvalidate/toolchain/identity qualification remain pending |
| Recovery | `RecoveryAsset` and `RecoveryAcquirer` implement bounded streaming hash, monotonic deadline, cancellation, disk-space preflight, safe symlink rejection, verified Apple redirect policy, and atomic temporary staging | S05 core hardening implemented with 13 focused regression tests; official product discovery and USB integration remain open (S12, G4) |
| Removable media | Device/write-plan contracts, typed confirmation and injected write callback | Preliminary guard; no real host adapters, live identity re-enumeration, integrated validation gate or safety tests (S12, G5) |
| CI and packaging | Windows/Linux Python 3.11/3.14 workflow and wheel/sdist smoke jobs defined; dev typing tools present; candidate source included; fresh local verification is 267 tests, 79.31% branch coverage and mypy clean across 84 files | Hosted results, clean-checkout evidence and packaged-workflow verification remain pending (S01, S13) |
| Workflow and hardware | CLI now exposes the shared schema-driven `ConfigurationService` through `Orchestrator`; exact-target fixture path, atomic configuration storage, redacted export and derived USB/ACPI evidence contracts are implemented | Textual TUI parity, real evidence capture, BIOS/installation guidance, distribution qualification and physical acceptance remain open (G6-G7) |

Fresh verification after the configuration-workflow slice on this Windows/Python 3.14.6 environment: `python -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79` = **267 passed**, **79.31% branch coverage**; `python -m mypy macloader tests` = **clean across 84 source files**; `python -m compileall -q macloader tests` and `git diff --check` completed cleanly apart from existing Git line-ending normalization warnings. The `macloader configure --version 15.0 --build 24A335 --fixture tests/fixtures/t480s/t480s_baseline.json --json` smoke path produced `thinkpad-t480s`, target `15.0/24A335`, `build_ready=false` and `accepted=false`, with acknowledgement, unresolved-policy and USB physical-evidence blockers as intended. These results establish local regression/type status, not correctness of untested safety paths or hosted clean-checkout behavior. The candidate `.gitignore` scopes root output to `/build/`, and the builder is included in the index; CLI imports it at module load.

No hosted CI, clean-checkout wheel test, live Recovery qualification, matching ocvalidate execution, USB write or physical installation was performed during this implementation slice. Existing application and documentation changes are preserved; the defects and qualification gates below remain open.

## Gate status

| Gate | Current state | What closes it |
|---|---|---|
| G0 foundation | **Reopened / mostly implemented** | S01-S02 and S06-S10 local repairs are implemented; clean-checkout verification and later validation gates remain |
| G1 policy/toolchain | Configuration contracts and candidate T480s/Sequoia policy/release records now exist; live machine/tool evidence remains pending | Frozen T480s/Sequoia policy, verified upstream artifacts and host tools |
| G2 T480s EFI | Prototype exists; **not valid for release** | S02-S04/S11 fixed; complete generated config/ACPI/identity and actual matching ocvalidate pass |
| G3 T480 variants | Detection fixtures exist; generated variants unqualified | Model-specific builds and negative validation matrix |
| G4 Recovery | Preliminary helper | S05/S12 plus official acquisition, CLI integration and real verified asset |
| G5 USB | Preliminary safety contract | S12 plus real adapters, re-enumeration, readback and sacrificial-media qualification |
| G6 workflow/distribution | CLI configuration slice partial; shared service boundary established | Textual TUI parity, guidance, licenses, clean-host packaging and documented commands |
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
| [/] S01 — In Progress; packaging | Scope `.gitignore` build-output rules to avoid ignoring `macloader/build/`; include builder sources and required new modules/resources in the reviewed commit. Check all intended source files, not only efi.py. A local wheel built from ignored working files is insufficient evidence. | Local committed-tree wheel and sdist smoke now pass from temporary environments: builder, Recovery and removable modules import; CLI help/probe/resolve/build/validate work outside the checkout. Hosted Windows/Linux matrix and clean-checkout evidence remain OPEN. First repair; unblocks reliable review/CI for all later work. |
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
| [/] S13 — In Progress; release evidence required; CI/docs | Keep README/START_HERE/handoff current; developer setup must install `.[dev]`. CI should collect branch coverage and publish results; establish measured thresholds for critical modules after adding negative tests rather than inventing a current percentage. | This implementation plan now records the fresh local 267-test / 84-file state; `.[dev]` installation and branch coverage remain enforced in CI, with a measured 79% minimum gate against the current 79.31% result and per-matrix `coverage.xml` artifacts. Hosted results, clean-checkout evidence and packaged configuration-workflow tests remain OPEN. Critical build/cache/recovery/USB failure cases must be tested regardless of aggregate percentage. G0/G6. |
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

Progress: versioned contract dataclasses and digests exist. The 2026-09-09 upstream catalog audit verified all pinned tags, RELEASE asset names/sizes/hashes and selected payload members, corrected nine stale DEBUG records and two license identifiers, pinned notice hashes, and hardened archive/URL selection. The host audit confirmed that `ocvalidate`, `iasl` and `macserial` were not installed on the host. The public toolchain audit now pins OpenCore 1.0.7 `Sample.plist`, `ocvalidate.exe`, `macserial.exe`, `macrecovery.py` and ACPICA 20260408 `iasl.exe`, with member hashes, source URLs and host architecture recorded in Research Item 010. These records are version-verified candidates only: no trusted loader, frozen live T480s/Sequoia policy, approved ACPI/USB selections or real config pass exists yet. Refine the existing contracts while closing S02; do not regenerate scaffolding.

- [ ] Capture a sanitized live T480s snapshot, BIOS settings/version, display/input/storage/WLAN variant and the exact OS target. Collect the ACPI/device-path and USB-port evidence required for generation. Missing hardware access blocks qualification, not independent implementation.
- [ ] Audit every selected release URL, version, SHA-256, actual archive layout, license and provenance against primary upstream sources. Run an opt-in real-acquisition qualification job and retain a manifest; mocked payload tests do not prove the committed catalog is correct. Qualify supported variants and strict offline replay.
- [ ] Pin matching OpenCore Sample.plist, ocvalidate, local identity tooling, ACPI compiler and recovery tooling for each supported host. The public candidate records and hashes are in Research Item 010; implement the trusted loader, acquire them into the controlled ignored workspace, and record host executable availability/architecture and compiler/tool provenance. Do not assume EFI binaries also supply every host tool.
- [ ] Specify versioned policies for SMBIOS, framebuffer/device properties, audio layout, input bus/plugins, battery/power, USB map and dGPU handling. Each policy requires a source, reason, applicable hardware/OS constraints, unresolved risks and physical checks. Address itlwm's user-facing connectivity requirements and how Recovery obtains network access.
- [ ] Introduce typed/versioned contracts for an executable BuildPlan, artifact lock, ACPI/config selections, identity reference, build manifest and validation report. Use stable model IDs and canonical content digests; timestamps and generated IDs must not define build equivalence. Preserve legacy preliminary-plan readability where practical.
- [ ] Resolve Tahoe audio/Wi-Fi/SMBIOS/storage questions before admitting a Tahoe build to qualification. Reassess PM981 risk from upstream evidence; selecting NVMeFix alone is not proof that storage is safe for installation.

Exit gate: one exact T480s/OS combination has no unresolved boot-critical policy; every selected artifact/tool is verified and every planned output has an unambiguous source and destination. Any remaining optional limitation has an explicit release policy and acceptance criterion.

### G2 — Generate and validate T480s EFI (v0.0.5 + early v0.0.7)

Depends on: G1. Owner role: EFI / ACPI implementation.

Progress: staging, selected extraction, identity/manifest code, pinned upstream license notices and CLI commands exist. Required repairs are S01-S04/S11. The current minimal plist, alphabetically sorted kext entries, fixed OpenCore/SMBIOS strings and unqualified toolchain remain to be replaced with qualified schema/policy, dependency order and validated tooling. ACPI and production USB mapping remain unfinished.

- [ ] Build in a fresh staging directory from the locked set. Map actual archive members to EFI/BOOT and EFI/OC destinations; extract only selected components and preserve licenses. Refuse collisions, missing bundles and unexpected executable content.
- [ ] Generate config.plist from the pinned schema with typed values, ordered kext/plugin registration, driver/ACPI entries, device properties, boot/NVRAM policy and explicit per-OS constraints. Include only components justified by the executable plan.
- [ ] Generate/compile the ACPI sources required by captured hardware. Verify namespace/device-path assumptions and compiler diagnostics; do not emit a universal bundle of model-name-based SSDTs. Include a reviewed production USB map.
- [ ] Generate unique local identity through the qualified tooling, store it privately and reuse it intentionally for rebuilds. Use fake identity fixtures for deterministic tests. Redact identities from logs, reports and public manifests.
- [ ] Complete the existing `build` and `validate` CLI services with structural/config/dependency/duplicate/version/identity checks and matching ocvalidate invocation. A missing validator, wrong version or nonzero result prevents a VALID label and promotion to a usable build.
- [ ] Publish EFI and sanitized snapshot, compatibility, lock/manifest, validation report and logs only after validation. Preserve failure diagnostics; do not overwrite existing EFI or partial output as if successful. Support explicit output paths and verified offline builds.

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

- [ ] Implement recovery listing/download through the pinned OpenCore `macrecovery.py` backend and Apple's infrastructure; resolve the requested target explicitly and record the returned product/build. Never silently fall back to another OS.
- [ ] Verify the image using the applicable upstream integrity/chunklist mechanism, record hashes/provenance and implement bounded retries, cancellation, disk-space checks and interrupted-download cleanup. Reuse verified local assets offline where supported.
- [ ] Bind recovery metadata and minimum size/network requirements to the selected EFI/OS policy. Explain required network access, including the chosen Ethernet/Wi-Fi route during Recovery.

Exit gate: mocked failure paths pass and an opt-in real download produces a verified, identified Recovery asset compatible with the qualification target. Actual boot is checked in G5/G7.

### G5 — Build guarded removable installation media (v0.0.9)

Depends on: G3 and G4. Owner role: host storage adapters / safety tests.

Progress: `RemovableMediaWriter` validates supplied device fields and confirmation before invoking an injected callback. It does not re-enumerate live devices or verify source artifacts, and real adapters are absent. Complete S12 and every item below; this is not yet a working USB writer.

- [ ] Implement separate Windows/Linux removable-device discovery and write adapters behind one contract. Enumerate whole-device identity, model, serial where available, exact capacity, partitions and system-disk relationship. Refuse internal/system/ambiguous devices.
- [ ] Provide a dry-run plan covering partitions/filesystems, EFI and Recovery placement, required capacity and data destruction. Require typed confirmation tied to the exact target. Re-enumerate identity/capacity/system role immediately before each destructive stage; hot-swap or renumbering invalidates confirmation.
- [ ] Gate writes on successful EFI validation, verified Recovery and adequate privileges. Do not silently elevate. Refuse unexpected mounted/busy targets; preserve existing EFI via a documented backup/confirmation policy where applicable.
- [ ] Handle cancellation, unplugging, disk-full and partial writes with explicit failure state. Verify files and hashes after writing, flush and safely unmount/eject; never label an incomplete device ready.
- [ ] Test discovery/safety/write sequencing using mocks and disposable disk images first, then explicitly selected sacrificial USB media on each supported host. Record the tested partition layout and firmware boot behavior.

Exit gate: negative safety tests prove no destructive call occurs for an unsafe/stale/unconfirmed target. A verified USB created on each advertised host boots the reference machine into Recovery. No internal disk is automatically modified.

### G6 — Finish the user workflow and distribution (v0.1.0 preparation)

Depends on: stable G2-G5 service contracts. Owner role: UX / packaging.

- [ ] Implement the specification's Textual TUI over the same services as the CLI: probe, compatibility explanations, target choice, plan review, acquisition/build progress, validation, recovery, USB confirmation and completion guidance. Keep cancellation and experimental/blocked states explicit.
- [ ] Add model/OS BIOS checklist, installation steps, booting installed macOS, safe EFI transfer guidance with backup/explicit target confirmation, limitations and troubleshooting. Installation remains a guided user action; do not silently write the internal EFI partition.
- [ ] Package a wheel/sdist with all policies/templates/resources, declared dev tooling and third-party notices. Resolve the project's own license with the maintainer. Test installation on clean Windows/Linux environments and configured workspaces; document host prerequisites and unsupported hosts.
- [ ] Finish structured redacted logs and a shareable diagnostic bundle; verify secrets cannot leak through nested evidence or errors. Define exit codes, JSON schema versions and recovery from interrupted operations.

Exit gate: a user can complete the whole flow from a clean install with no source checkout or hand-edited EFI. CLI/TUI share validation and safety rules, packaging smoke tests pass, and docs match actual commands.

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
