# T480s configuration workflow — implementation handoff

Date: 2026-09-09. Status: planning complete; implementation not started by this task.
Repository baseline inspected for this handoff: `55b5e976a56317ec7c438df99fbde9a61fd30add`; working tree was clean before documentation edits.

## 1. Instructions for the receiving agent

Implement a schema-driven, guided in-app configuration workflow for the user's Lenovo ThinkPad T480s. This document records the approved design defaults and the proposed implementation sequence. The handoff recipient's user instruction determines when implementation starts; this documentation update itself does not authorize physical disk writes or installation.

Read in this order:

1. This document and ADR-007 in [DECISIONS.md](DECISIONS.md).
2. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), including G0–G7 and the issue register.
3. [ENGINEERING_SPEC_V0.1.md](ENGINEERING_SPEC_V0.1.md).
4. [RESEARCH_LEDGER.md](RESEARCH_LEDGER.md), especially the current toolchain and Recovery records.
5. `macloader/domain/`, `macloader/build/efi.py`, `macloader/orchestrator.py`, `macloader/ui/cli.py`, `macloader/database/data/`, `macloader/recovery/`, `macloader/removable/`, and relevant tests.
6. `tools/capture_t480s_evidence.ps1`, then the existing architecture, support and hardware acceptance documents.

Recheck the checkout and any applicable AGENTS.md before editing. Preserve unrelated changes. Reconcile historical status summaries with source and actual evidence: old test counts and milestone labels are not fresh verification. Do not repeat work already completed elsewhere.

Do not ask the user to approve decisions A1–A8 again. Ask only for missing concrete inputs, a material departure from these defaults, or a physical action requiring contemporaneous confirmation. Continue independent software work while hardware inputs are pending. If exhaustive variant research is necessary, the user authorized low-budget Luna research agents for that bounded work; this is not blanket authorization for implementation swarms.

## 2. Approved defaults

| ID | Approved decision | Remaining input, not a reopened design decision |
|---|---|---|
| A1 | Sequoia first; the user's T480s is the reference. Optional variants do not inherit qualification. | Exact macOS version/build, actual hardware/BIOS and optional peripherals to qualify. |
| A2 | Reviewed experimental profiles require explicit acknowledgement and all applicable evidence, validation and media gates. | Profile-specific acknowledgement at use; no waiver of blockers. |
| A3 | Advanced controls are reviewed presets and bounded typed values only. | Which candidate values have sufficient policy evidence to expose. |
| A4 | Unknown optional devices stay visibly unsupported; proceeding requires a reviewed policy establishing a safe installation path. | Device identity and safe omission/disable evidence. Unknown critical hardware blocks builds. |
| A5 | Recovery mismatches block progression and require explicit target reselection. | An available exact Recovery target and its permitted relationship to the requested installation target. |
| A6 | Deliberate machine-associated identity reuse, protected local storage and separate private backup. Shareable configurations contain no identity secrets. | Generate/reuse choice and a local identity reference; never request secrets in chat. |
| A7 | Historical policy replay is deferred. Saved configurations are re-evaluated against current policy. | Review changed selections and regenerate locks when policy changes. |
| A8 | Windows qualification first, Linux second; Textual installed by default. Each host's media writing remains disabled until its adapter is qualified. | Access to test hosts and sacrificial media. |

Scope remains T480s-first within the existing T480/T480s project. Preserve T480 behavior and regression coverage, but do not expand this work into T480 qualification. Sonoma/Tahoe records remain separately restricted; a YAML entry is not a support claim. No firmware modification, automatic root patching, shared identities, arbitrary EFI editing or silent internal-disk writes.

## 3. Architecture and existing contracts

Use Textual and Click as presentations over the same Orchestrator services:

```text
TUI / CLI
  → configuration service + versioned policy/options
  → observations + confirmations + evidence + exact target
  → accepted immutable configuration
  → existing BuildPlan → existing resolver → ArtifactLock
  → EFI builder + trusted ToolchainSelection + private IdentityReference
  → ValidationReport + redacted BuildManifest
  → verified Recovery bundle → existing media safety service
```

Reuse the current dataclass/domain approach, dependency catalog, archive/cache integrity checks, canonical digest helper, toolchain/identity contracts and validation gates. Do not build a second dependency resolver or UI-only validation system.

Known implementation gaps from source inspection:

- BuildPlan currently uses a product-level macOS target and derives readiness from support/unresolved requirements. Exact target and configuration/evidence bindings must be added explicitly.
- HardwareSnapshot contains inventory uncertainty, but not a complete per-field confirmation/override history.
- EFI `_write_config`, fixed SMBIOS policy and random-token identity generation are prototypes to replace, not qualified defaults to expose.
- Existing lock equality checks compare selected subcomponents against catalog specifications. Optional plugin selection requires a coordinated catalog/resolver/builder contract change, not mutation of a resolved lock.
- Toolchain provenance supplied by a caller must not establish qualification. Use trusted records and verified tool bytes.
- Recovery and removable guards exist. Production discovery, host adapters and end-to-end qualification remain separate deliverables.
- The existing PowerShell collector is an input adapter candidate, not proof of complete USB/ACPI evidence. Its pending OS defaults must never satisfy exact-target validation.

Keep compatibility, evidence, software validation and physical acceptance separate. Retain CompatibilityState values. Additional evidence states are `missing`, `partial`, `complete`, `conflicting`, `stale`; confidence is independent of completeness. Report software validation as not-run/structural-only/passed/failed and physical acceptance as not-tested/failed/accepted, bound to the tested configuration.

## 4. Hardware and option coverage

Every field presents detected value/source, user confirmation or override, effective value, applicability and unresolved evidence. User overrides never erase observations. A policy default is a suggestion, not a detection result. Confirmed absence requires evidence appropriate to that category; failed enumeration remains unknown.

| Area | Fields and behavior |
|---|---|
| Machine | Model, machine type, CPU identity/generation, BIOS revision/date and required BIOS confirmations. Contradictory model evidence blocks; no fuzzy T480/T480s substitution. |
| Display/touch | Panel identity when available, resolution, internal/external role, touch present/absent/unknown, controller/bus IDs. Do not infer touch from resolution. External display requirements have their own profile/evidence. |
| Graphics | iGPU PCI identity, any dGPU, relevant display outputs. Unexpected dGPU means unresolved variant; never silently import T480 dGPU handling. |
| Storage | Exact model, interface, firmware if available, intended installation disk and replacement status. Generic NVMe matching or selecting NVMeFix does not qualify a risky specific model. Selecting a target disk is not write authorization. |
| WLAN/Bluetooth | Separate PCI/USB identities, original/replaced/unknown status, reviewed driver strategy and limitations. Reject mutually exclusive stacks. Installed-OS WLAN support does not prove Recovery networking. |
| Input | Keyboard layout, keyboard/trackpad/TrackPoint bus and identifiers, touchscreen and fingerprint presence. Fingerprint functionality stays unavailable without reviewed policy; presence alone need not imply safe omission. |
| Audio/Ethernet | Codec evidence distinct from controller evidence, bounded audio-layout candidates, Ethernet identity and intended Recovery network path. |
| Other | USB controllers/internal devices, webcam, Thunderbolt/dock scope, battery-related evidence and peripherals relevant to acceptance. |

Offer `unknown/other` to record hardware observations, not to manufacture selectable supported components. Research factory variants separately from aftermarket changes. Current T480s YAML is not an exhaustive manufacturer inventory. Verify candidate facts with primary Lenovo/upstream sources before adding policy, recording source/date/confidence and qualification scope in the research ledger.

OpenCore options include policy-filtered SMBIOS, kext/plugin/driver sets, ACPI profile, graphics/device properties, audio layout, NVRAM and boot profiles. Required dependencies are visible but locked. Boot settings, debugging and picker choices use allowlisted values. No free-form boot arguments, arbitrary plist keys, binary property blobs or arbitrary imported ACPI execution in the guided flow. Per-OS and exact-build restrictions belong to policy data.

## 5. Domain model, binding and persistence

Proposed new contracts:

| Contract | Required content |
|---|---|
| UserConfiguration | Schema, ID/revision, sanitized snapshot reference, confirmations/overrides, exact target, option IDs, evidence references, Recovery request, private identity reference, acknowledgements. Drafts can be incomplete. |
| HardwareObservation / HardwareConfirmation | Field path, value, source/provider/version, evidence reference, observation status; confirmation action/value/reason retained separately. |
| MacOsTarget | Product ID/name, exact version, exact build, release-record digest. No latest/default alias in accepted inputs. |
| OptionDefinition | Stable ID, value type/control hint, allowed values, applicability, default rule, dependencies/conflicts, evidence requirements, support state, explanation/research references. |
| EvidenceRecord | Kind/schema, digest, private local reference, machine/BIOS binding, capture method/version, completeness/confidence, unresolved checks. |
| ConfigurationIssue | Stable code, field path, severity, blocking stage, rule ID, explanation and remediation. |
| AcceptedConfiguration | Immutable normalized selections and transitive bindings, derived semantic digest. Not independently trusted merely because serialized. |
| Acknowledgement | Rule/option ID, exact warning digest and configuration binding; never a global allow-unsafe boolean. |
| RecoveryResolution | Requested target, resolved Recovery product/version/build, image/chunklist metadata and integrity evidence, target relationship policy. |

Extend existing BuildPlan with exact target, stable model ID, accepted-configuration digest, sanitized hardware-content/evidence digests and profile/policy bindings. Preserve existing readable fields where needed for compatibility. Extend existing ToolchainSelection, BuildManifest and ValidationReport rather than introducing parallel equivalents. IdentityReference remains opaque and redacted; bind the private identity digest at the existing manifest/build boundary.

Freeze accepted inputs deeply: tuples/immutable mappings or defensive copies with strict validation. A frozen dataclass containing mutable lists/dicts does not solve mutation races. Public builder/service boundaries revalidate accepted input bindings and current policy; callers cannot clear unresolved requirements to bypass evidence.

Resolution sequence:

1. Strictly parse a draft and load the current trusted policy bundle.
2. Reconcile observations/confirmations and evaluate exact-target applicability.
3. Validate options, evidence and required acknowledgements; return structured issues.
4. Produce accepted inputs and derive BuildPlan capabilities/components through shared services.
5. Resolve dependencies using the existing resolver; preserve exact artifact and selected-plugin binding.
6. Recheck policy/configuration/evidence/toolchain bindings at acquisition, build, validation and media stages.

Do not create digest cycles. Configuration semantics bind requested profiles/evidence/policy; BuildPlan binds accepted configuration; artifact lock binds BuildPlan; final build binds lock, identity and toolchain; validation binds output. Separate acknowledgement/review receipts from semantic content they acknowledge. Exclude volatile timestamps, UI focus and absolute local paths from semantic digests; retain provenance in audit records. Preserve old digest meaning under old schemas and use the shared canonical helper.

YAML authoring layout:

- Existing models/components/macos/dependencies retain their purposes.
- `macos/releases.yaml`: exact version/build records.
- `configuration/t480s.yaml`: typed option definitions and constraints.
- `profiles/t480s/*.yaml`: reviewed configuration transformations and applicability.
- `evidence/t480s.yaml`: required evidence checks and invalidation rules.
- `toolchains/catalog.yaml`: host tool/source/schema hashes and trusted status.
- `recovery/catalog.yaml`: permitted target relationships/discovery policy.

Use a bounded declarative predicate vocabulary, never eval or executable expressions. Reject duplicate keys/IDs, unknown behavior fields, broken references, rule cycles and contradictory choices. Bundle digests cover transitive profile/schema/source inputs. A digest proves identity, not authority: imported user YAML cannot declare itself trusted policy.

Save public configurations as versioned JSON with atomic replacement, revision conflict detection and a previous-version backup. Keep identities and raw evidence in protected local storage outside the repository. Export sanitized configurations/evidence summaries by default; missing evidence after import remains missing. Reject escaping/symlink paths and enforce import size/depth limits. Do not include raw tool output by default if it may expose identifiers.

Migration proposal: introduce configuration schema `1`; increment changed existing contract schemas explicitly after auditing consumers. Legacy product-only plans import as drafts with exact-target/evidence issues. Never invent missing facts or trust serialized readiness. Unknown future schemas fail with a contextual error. Re-evaluate against current policy; show a migration/selection diff and regenerate the lock. Historical replay is deferred under A7.

## 6. Evidence, Recovery and identity

### USB

Record controller identity, physical socket label, logical port, connector type, tested speed, USB-C orientation where applicable, internal-device association, observation and capture tool/version/digest. Policy determines required tests and controller constraints; do not hardcode unsupported claims into widgets.

Require physical observations of applicable ports/speeds/orientations, internal USB associations, explained logical ports and explicit dock/Thunderbolt scope. Imported maps remain candidates until matched evidence is verified. Completeness is derived; no user checkbox can mark an untested map complete. Conflicts and BIOS/controller changes invalidate dependent evidence.

Provide a pre-install capture/import route to avoid depending solely on a completed macOS installation. Any bootstrap mapping is a separately marked development artifact; it cannot qualify a production map or bypass production media gates. Physical port evidence and later macOS behavior acceptance are separate requirements.

### ACPI

Capture/import original tables with hashes, tool/version and BIOS/machine association. Preserve originals privately; assess sanitization before export. Validate table shape and required namespace paths. Resolve reviewed ASL sources against observed paths, compile using pinned iasl and record source/compiler/output digests and diagnostics. Imported AML or successful compilation alone does not prove applicability. Do not execute arbitrary imported code or dynamically rewrite arbitrary DSDTs.

### Recovery

Separate installation target, requested Recovery environment, server-resolved Recovery build and observed installed OS. The ledger notes that upstream default/latest discovery is not an exact Sequoia selector. Verify actual product/build metadata and authenticated image/chunklist evidence; hashing downloaded bytes alone does not authenticate them.

Retain existing HTTPS/redirect, bounded streaming, timeout, cancellation, disk-space, integrity and atomic-publication guards. If Apple cannot supply an approved exact relationship, block and offer explicit reselection. A Recovery build does not prove the installed OS build; verify the installed version/build during physical acceptance.

Explain build-host download connectivity separately from Ethernet/WLAN availability inside Recovery. An installed-OS companion application must not be assumed available in Recovery. Policy must establish at least one usable Recovery network route when required.

### Identity

Generate locally with qualified tooling late in the build, or deliberately reuse a machine-associated local identity compatible with the selected SMBIOS. Store serial, MLB, UUID and ROM privately with verified Windows ACL/POSIX protection. Do not key reuse solely to a changing dependency lock. Policy governs ROM and required identity fields.

Mask UI values; logs/manifests/exports carry opaque references and digests only. Redact subprocess errors and transformed representations, not just ordinary successful output. Real EFI config necessarily contains operational identity values: treat build output and media as private, and exclude them from shareable diagnostic bundles. Private identity backup is separate from public configuration export. Tests use clearly fake identities only.

## 7. UI and CLI workflow

| Step | Controls | Gate/feedback |
|---|---|---|
| Start/resume | New/resume/import, policy version, migration diff | Imported state is revalidated, never trusted as previously qualified. |
| Machine | Probe/import and model/BIOS summary | Unknown/contradictory machine identity blocks accepted plan. |
| Hardware | Grouped dropdowns, present/absent/unknown radios, confirmation/override reason | Show detected and effective values side by side with source/confidence. |
| macOS | Product → exact version → exact build | Product-only draft allowed; no reproducible build until all three selected. |
| Profiles | SMBIOS, graphics, audio, input, network, ACPI, boot profile | Required components locked; disabled choices explain policy reasons. |
| Evidence | USB session/checklist and ACPI capture/import | Show missing tests and stale/conflicting evidence. |
| Recovery/network | Requested/resolved targets and network route | Mismatch or missing route blocks installer readiness. |
| Identity | Generate/reuse, masked local label | No identity secrets in ordinary fields or exports. |
| Review | Exact inputs, selections, dependency versions, changes, evidence, limitations | Specific experimental acknowledgements; material edits invalidate review. |
| Build/validate | Progress, cancellation, sanitized per-stage results | Structural-only is not ready-to-install. |
| Media | Fresh devices, vendor/model/path/capacity, warning and typed confirmation | Re-enumerate, revalidate source, write, then verify readback. |
| Acceptance | BIOS required/recommended checklist and hardware results | No firmware edits or automatic support promotion. |

Use text labels as well as color. Keep unavailable choices inspectable. Cancel slow operations through service cancellation hooks; avoid blocking the Textual event loop. Autosave only drafts and never repeat a destructive action on resume. A saved device selection or typed confirmation is not reusable write authorization.

Proposed CLI surface (names may be adjusted consistently): `config new/show/set/check/import/export/migrate`, `evidence usb/acpi`, `plan --config`, `build --config`, `recovery list/resolve/download`, `usb list/plan/write`. Extend existing commands rather than duplicate their services. Product-only legacy plan commands may produce preliminary plans; build commands require exact accepted inputs. JSON results expose the same issue codes and selections as TUI. Do not add an unrestricted `--force` or secrets on command lines.

## 8. Implementation phases and completion evidence

| Phase | Tasks | Dependencies and exit evidence |
|---|---|---|
| P0 Baseline/scope | Reconcile current source and G/S status; run baseline checks; inventory fields/rules; inspect collector; record unresolved research. | First. Deliver baseline report, field inventory and test evidence. No fabricated current test counts. |
| P1 Contracts/storage | Add typed draft/accepted/evidence/exact-target contracts; strict schemas, migrations, immutable bindings, atomic store/redacted export. | P0. Round-trip/negative/migration/digest tests pass; old input remains draft when incomplete. |
| P2 Policy/services | Add declarative options/profiles and shared configuration service; structured issues; bind BuildPlan/resolver selections. | P1. Deterministic selection, conflict checks and direct API bypass tests pass. Unknown facts remain blocked/unresolved. |
| P3 Evidence | Per-field provenance; USB and ACPI capture/import/completeness; sanitized fixtures. | P1–P2. Synthetic tests prove invalidation; physical completeness remains pending until supplied. |
| P4 EFI/toolchain/identity | Trusted loader, reviewed schema transformations, ACPI compilation, exact plugin selection, qualified private identity generation/reuse. | P2–P3 and relevant G1 inputs. Actual matching ocvalidate/iasl records; no qualification from stubs or caller flags. |
| P5 Recovery | Discovery/resolution, authenticated integrity, exact target binding, network requirements. | P1–P2 plus trusted Recovery tooling. Mock matrix and opt-in real acquisition recorded; unavailable exact builds block. |
| P6 TUI/CLI | Implement screens and parity, progress/cancel, review invalidation and draft resume. | Can start after P2 with fixtures; completion needs P3–P5. Equivalent interactions yield identical semantic plan/lock digests and errors. |
| P7 Media/packaging | Qualified Windows adapter first, Linux second; guarded integration/readback; wheel/sdist and clean-host TUI tests. | P4–P6 and G4/G5 prerequisites. Disposable tests, clean installed tests and sacrificial-media acceptance. Unqualified host write capability disabled. |
| P8 Physical qualification | Freeze exact machine/BIOS/OS/policy/tools/output; run full acceptance; review narrowly scoped promotion. | P7 plus human hardware work. Record actual results; only matching accepted scope may become SUPPORTED. |

First implementation slice: P0, then P1 and one P2 fixture-backed path from draft to structured issues and preliminary BuildPlan. Do not start by building dropdowns around prototype builder defaults. Continue independent phases when physical work is unavailable; record the blocker without marking the gated phase complete.

### Current implementation status — 2026-09-09

- **P0 — verified:** Reconciled the current checkout, existing contracts, source/tests, collector and research/toolchain status. Fresh baseline: 254 tests passed, 79.79% branch coverage against the 79% gate, and mypy clean across 72 source files on Windows/Python 3.14.6. No `AGENTS.md` was present. Existing documentation edits were preserved.
- **P1 — implemented and verified for this slice:** Added exact `MacOsTarget`, immutable configuration/evidence/confirmation/acknowledgement/issue contracts, strict duplicate-key policy loading, schema-1 migration boundary, revision-aware atomic public JSON storage and redacted export behavior. Thirteen focused workflow tests pass.
- **P2 — implemented and verified for the first fixture path:** Added versioned T480s option/profile/evidence policy data and an exact Sequoia candidate release record. The shared `ConfigurationService`, `Orchestrator`, and `macloader configure` command reconcile the sanitized baseline fixture into a preliminary BuildPlan with exact target, model, hardware-content, evidence and profile bindings. Focused workflow, contract and CLI tests pass.
- **P3 — software sub-slice implemented; hardware verification pending:** Added derived USB evidence sessions and metadata-only ACPI evidence contracts. USB completeness requires unique physical labels/logical ports, tested speed and USB-C orientation where applicable; a complete record is generated by the session and carries physical evidence provenance. No collector output or checkbox can establish physical qualification.
- **Current verification:** 267 tests passed, 79.31% branch coverage against the 79% gate, mypy clean across 84 source files, and `compileall`/`git diff --check` clean (Git reported only existing LF/CRLF normalization warnings for tracked documentation/source files).
- **Remaining gates:** The fixture path intentionally remains blocked by missing experimental acknowledgements, unresolved framebuffer/audio policy, and absent physical USB-port evidence. P3 real capture/import, P4 trusted toolchain/EFI qualification, P5 exact Recovery acquisition, P6 Textual parity, P7 qualified media adapters and P8 physical acceptance remain open. The catalog’s Sequoia 15.0/24A335 record is a selectable candidate, not a claim of T480s qualification.

For each phase, update this document with files changed, tests actually run, remaining blockers and the next concrete action. Map evidence back to G0–G7 rather than inventing a parallel release gate system. The workflow does not close unrelated shipping defects automatically.

## 9. File-by-file change map

Paths below are repository-relative. Proposed new modules require package initializers as appropriate; adapt names if current source has evolved, preserving responsibilities.

| File(s) | Planned responsibility |
|---|---|
| `domain/configuration.py`, `domain/evidence.py`, `domain/targets.py` (new) | New typed contracts. |
| `domain/hardware.py`, `domain/build_plan.py`, `domain/contracts.py`, `domain/dependencies.py`, `domain/__init__.py` | Provenance, exact target, schema/digest/manifest/lock extensions. |
| `configuration/service.py`, `store.py`, `migrations.py` (new) | Shared options/acceptance, safe persistence and migrations. |
| `database/schema.py`, `database/loader.py` | Strict new schemas, cross-reference validation and bundle identity. |
| `database/data/models/t480s.yaml`, `components/*.yaml`, `macos/*.yaml` | Reviewed hardware applicability and exact-target restrictions; no generic support promotion. |
| `database/data/macos/releases.yaml`, `configuration/t480s.yaml`, `profiles/t480s/*.yaml`, `evidence/t480s.yaml`, `toolchains/catalog.yaml`, `recovery/catalog.yaml` (new) | Versioned release/options/profile/evidence/tool/Recovery records. |
| `database/data/dependencies/catalog.yaml` | Reviewed dependencies and allowed plugin sets; preserve artifact/license hashes. |
| `compatibility/engine.py`, `compatibility/report.py`, `dependencies/resolver.py` | Configuration-aware evaluation and exact selection binding. |
| `detection/windows.py`, `detection/linux.py`, `detection/sanitize.py` | Field observations and redaction. |
| `tools/capture_t480s_evidence.ps1` | Reuse/adapt collector; validate completeness and sanitization instead of duplicating it. |
| `evidence/usb.py`, `evidence/acpi.py` (new) | Capture/import and derived evidence status. |
| `toolchain/loader.py`, `identity/service.py` (new) | Trusted tool selection and protected identity lifecycle. |
| `build/config.py`, `build/acpi.py` (new), `build/efi.py` | Reviewed config/ASL processing; replace prototypes; preserve staging and integrity. |
| `recovery/discovery.py` (new), `recovery/acquirer.py` | Exact Recovery resolution and authenticated evidence. |
| `removable/writer.py`, `removable/windows.py`, `removable/linux.py` (new adapters) | Existing guards plus qualified host operations and full payload readback. |
| `orchestrator.py` | Single service facade for both clients. |
| `ui/cli.py`, `ui/tui/app.py`, `screens.py`, `widgets.py` (new TUI) | Commands and presentation; no separate policy engine. |
| `diagnostics/logging.py` | Structured redacted diagnostics. |
| `pyproject.toml`, `tests/package_smoke.py`, existing CI workflow(s) | Textual default dependency, all new resource types including reviewed ASL, installed-host tests. |
| `docs/ARCHITECTURE.md`, `DECISIONS.md`, `IMPLEMENTATION_PLAN.md`, `SUPPORT_MATRIX.md`, `HARDWARE_ACCEPTANCE.md`, `RESEARCH_LEDGER.md` | Keep architecture, status, rationale and actual qualification evidence current. |

Add focused unit suites for configuration contracts/store/migrations, option resolution, provenance, evidence, toolchain, identity and exact Recovery. Add `tests/integration/test_configuration_workflow.py`, TUI interaction tests, CLI parity tests and packaging fixtures. Extend existing build/resolver/contract/sanitization/media regression suites rather than replacing them.

## 10. Safety and acceptance matrix

| Area | Automated proof | Human/real-environment proof |
|---|---|---|
| Schema/import | Bad shapes/types, duplicate keys, unknown IDs/schema, oversized imports, traversal/symlinks, forged readiness rejected. | Review migration changes where selections differ. |
| Provenance | Failed probe vs confirmed absence; conflicting override; stale snapshot; no silent promotion. | Confirm actual replacements and physical device observations. |
| Variant matrix | T480s baseline/touch; machine types; CPU/iGPU; alternate storage/WLAN/input/audio; unexpected dGPU; incomplete inventory. Keep T480/unsupported-model regressions. | Sanitized real fixtures identified separately from synthetic cases; research factory claims. |
| Policy | Conflicting WLAN/input stacks, missing plugins, OS/SMBIOS mismatch, invalid properties and unacknowledged experimental profiles blocked. | Reviewed option policy and sources. |
| USB evidence | Missing socket/speed/orientation/internal association, duplicate ports, wrong controller and stale BIOS cannot become complete. | Actual physical tests and later macOS port behavior. |
| ACPI | Wrong BIOS/namespace, malformed tables, missing tool, hash mismatch, iasl failure/timeout block. | Machine capture and actual pinned compilation; physical behavior acceptance. |
| EFI | Fake-identity golden fragments, exact file/plugin registration, changed locks/tools/output, wrong/missing/failed/timed-out validator rejected. | Actual matching ocvalidate pass; compiler/tool provenance. |
| Identity | Canary secrets absent from logs, manifests, exports and diagnostics including encoded variants; ACL/store failures block. | Deliberate local generate/reuse and private backup. |
| Recovery | Exact mismatch, bad signature/chunk/hash, unavailable target, redirects, deadline/cancel preserve existing guards. | Verified acquisition, Recovery boot/network and observed installed OS version/build. |
| Staleness | Policy/catalog/profile/evidence/tool changes invalidate review/locks; acknowledgements cannot authorize changed risks. | Explicit review of newly resolved target/selections. |
| TUI/CLI | Same semantic configuration/plan/lock and issue codes; keyboard navigation; progress/cancel; safe resume. | Usability on supported terminal/host. |
| Media | No write callback on system/internal/nonremovable/mounted/read-only/ambiguous/stale/undersized target, bad confirmation/source/validation; interruption/readback failure handled. | Qualified adapters, sacrificial media, fresh typed confirmation, complete EFI/Recovery readback. |
| Packaging | Fresh Windows/Linux wheel/sdist outside checkout, packaged YAML/ASL/resources, CLI/TUI startup, no dev-tree dependencies. | Clean-host qualification; Windows before Linux. |
| Physical | Acceptance recorder binds all exact digests without inventing results. | Picker, Recovery, installer, installed boot, iGPU/resolution/brightness, keyboard/trackpad/TrackPoint, battery, audio/mic, Ethernet/WLAN/BT, USB-A/C, webcam, sleep/wake/restart/shutdown; touch/dock/Thunderbolt as scoped. |

Retain current tests/type/coverage gates; record fresh results and meaningful new failure-path coverage. Stubs and synthetic fixtures establish software behavior only. No test suite count, generated map, successful copy, compiler pass or ocvalidate pass alone establishes physical support.

## 11. Ownership, missing inputs and unresolved decisions

Agent work: contracts, services, policy research/proposals, UI/CLI, safe persistence, fixtures, tests, pinned tool acquisition/execution on available hosts, adapters, packaging and redacted evidence reports. Human work: actual hardware/BIOS details, port tests/peripherals, required local access, target selection, private identity choice, sacrificial media, boot/install and physical acceptance.

Required human inputs should be requested when the dependent phase needs them:

- Sanitized T480s inventory and BIOS revision, including replacements and touch/display/peripheral scope. Use the collector as a starting point and inspect output before sharing.
- Explicit exact Sequoia version/build; availability must be verified, not assumed.
- USB physical observations and ACPI capture associated with that machine/BIOS.
- Recovery network route and access to the test machine/hosts.
- Local identity generate/reuse choice, without exposing real values in conversation.
- Backed-up sacrificial media and contemporaneous confirmation before any destructive operation.

A1–A8 are settled. Remaining technical investigations include exact hardware option inventory, reviewed graphics/audio/ACPI/SMBIOS values, pre-install USB evidence tooling, exact Recovery availability, and authoritative tool trust records. Choose routine module/schema implementation details without reopening approved defaults; record them in ADRs. Seek approval for a scope change such as root patching, unrestricted editing, historical policy execution, extra OS qualification, changing identity privacy, or weakening a gate. Such changes are not implied by this handoff.

## 12. Handoff completion contract

An implementation report must state the completed phases, files, actual test/tool evidence, outstanding human inputs and exact remaining gates. Never describe this plan or fixture-generated output as a qualified T480s configuration. SUPPORTED requires hardware evidence, real toolchain validation and physical acceptance for the exact recorded scope.
