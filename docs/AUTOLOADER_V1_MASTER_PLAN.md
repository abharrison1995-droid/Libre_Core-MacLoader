# Libre_Core MacLoader — v1 True Autoloader Master Plan

**Planning baseline:** 2026-10-03

**Repository:** abharrison1995-droid/Libre_Core-MacLoader

**Audited checkout:** branch main, HEAD a93705e4d00f0c4be7a4eb3a806381bbe4629f0f

**Campaign under design:** ThinkPad T480s 20L8, BIOS N22ET85W 1.62, Sequoia 15.0 build 24A335, reviewed MacBookPro15,2 profile
**Scope:** software architecture and implementation plan only. No physical media write, USB boot, or Recovery boot was performed for this planning pass.

The checkout had no staged changes. The working tree already contained owner changes in `docs/PROJECT_STATUS.md`, `docs/T480S_FIRST_INSTALL_RUNBOOK.md`, and `macloader/database/data/configuration/t480s.yaml`. Those files were preserved. This plan is the only new file from the planning pass. The local YAML change selects ALC257 layout 86, matching the reviewed P4 profile; it resolves the previously reported layout-11 mismatch in this checkout.

## A. Executive summary

MacLoader has the core engineering machinery for a careful Hackintosh preparation workflow: normalized hardware snapshots, compatibility and configuration policy, evidence contracts, private identity handling, pinned tools and dependencies, EFI construction and validation, Recovery verification, and guarded removable-media write infrastructure. The project does not need a replacement architecture.

The missing product layer is a persistent, resumable campaign orchestrator that decides the next safe action and runs existing services until an actual human checkpoint or blocker. Today the CLI and Textual TUI expose many individual operations and internal identifiers. The workflows are not joined into one guided path; observations are not yet reconciled into configuration; ACPI directories and USB JSON are still imported by the operator; and build/media artifacts are not advanced as a campaign session.

The main design is an AutoloaderService above the current WorkflowService, configuration, build, Recovery and removable-media services. It loads one reference campaign policy that links existing versioned records, persists a private session, recomputes a structured NextAction, and runs safe, idempotent steps. A Guided UI and an Engineering UI become clients of the same semantic service.

The local ALC257 default is already corrected to layout 86. Two other material implementation gaps must remain explicit: USB observations currently do not generate or configure an actual port map, and neither host has a physically qualified writer. Those are readiness gates, not UI polish. Exact Recovery for 24A335 is also still externally blocked. A separate, conspicuously non-qualifying smoke-test mode is possible only if its provenance and restrictions remain distinct from the exact Recovery lock.

The goal is deliberately bounded: prepare and validate installer media, then guide and record a picker/Recovery smoke test. It must stop before erase, partition, or macOS installation on the internal disk. The T480s stays experimental until the existing hardware acceptance suite is complete.

## B. Current-state audit

“Implemented” means the relevant contract or subsystem exists. It does not imply that the guided end-to-end campaign is implemented or physically qualified.

| Requested capability | Current classification | Evidence in this checkout |
|---|---|---|
| Linux and Windows hardware providers; normalized and sanitized snapshots | Partially implemented | `macloader/detection/linux.py`, `windows.py`, `normalize.py`, `sanitize.py`; `macloader/domain/hardware.py`. Both providers collect useful machine, BIOS, CPU, graphics, network and storage facts. Display records exist in the domain model but providers do not populate panel/touch evidence; codec and Thunderbolt detail is incomplete in places. Windows also probes storage serial, which must remain private and be sanitized before persistence/export. |
| Hardware observations and confirmations | Implemented as contracts, not wired | `HardwareObservation`, `HardwareConfirmation` and `ObservationStatus` are in `macloader/domain/configuration.py` and serialize with `UserConfiguration`. No production observation producers/reconciler currently feed them into configuration decisions. Do not create a second fact system. |
| Compatibility, exact target, schema-driven config and build plan | Implemented | `macloader/compatibility/`, `macloader/domain/build_plan.py`, `macloader/configuration/service.py`, `macloader/database/data/macos/releases.yaml` and `macloader/database/data/configuration/t480s.yaml`. These are lower-level capabilities, not an automatic candidate selector. |
| T480s P4 profile and ALC257 layout | Implemented; local fix already present | `macloader/database/data/profiles/t480s/p4-sequoia.yaml` specifies layout 86. The modified local `configuration/t480s.yaml` now defaults to layout 86. `macloader/build/config.py` overlays the effective audio selection into the generated plist (`layout-id` and `alcid`). `tests/unit/test_readiness_repairs.py::test_effective_audio_selection_changes_generated_plist` covers explicit selection, but a regression should cover default YAML → configuration → generated plist. |
| Single authoritative exact-machine campaign | Absent; exact assumptions are scattered | Machine/BIOS/target/profile/evidence assumptions appear in `configuration/t480s.yaml`, `profiles/t480s/p4-sequoia.yaml`, `evidence/t480s.yaml`, `macos/releases.yaml`, `recovery/catalog.yaml`, `WorkflowService`, `ConfigurationService`, `build/config.py`, `build/acpi.py`, and identity/Recovery helpers. `Database` loads several catalogs but no reference-campaign registry. A new campaign should link those records, not duplicate graphics/audio/dependency values. |
| Configuration persistence and revisions | Implemented, but no campaign session | `macloader/configuration/store.py` uses atomic compare-and-swap writes and redacted exports. `WorkflowService.create` produces a draft; snapshots are persisted privately against a configuration UUID and resume requires that UUID. There is no automatic machine-bound create/resume path. A few active drafts without snapshots were noted in the updated local status document. |
| Acknowledgements | Strong low-level binding, not guided | `ConfigurationService.acknowledge` binds rule, warning digest, and configuration digest; relevant configuration changes invalidate the acknowledgement. The TUI still asks for raw rule IDs and warning text. One guided action should acknowledge the campaign’s reviewed warning set only after the effective configuration is settled. |
| ACPI evidence validation/import | Implemented, but manual source path | `macloader/build/acpi.py` and `macloader/evidence/acpi.py` privately import and validate DSDT plus the expected 11 SSDTs, including table header/checksum/size and BIOS/snapshot binding. `WorkflowService.import_acpi_capture` requires a source directory and contains an exact-machine assumption. There is no `AcpiCaptureProvider`; the operator owns the path. The Windows runbook relies on a separate script and separately supplied `acpidump.exe`. |
| USB evidence contract | Implemented, no collector or generated map | `macloader/evidence/usb.py` defines `UsbPortObservation` and `UsbEvidenceSession`, including physical labels, logical route, controller, speed, USB-C orientation, duplicate checks and unresolved states. There is no hotplug wizard/provider. The TUI imports JSON. More importantly, profile USB route metadata is not consumed to generate a real port map or USB mapping kext/config; the evidence is not yet an effective build input. |
| Toolchain and dependency acquisition | Implemented below the guided layer; host catalog incomplete | `macloader/toolchain/loader.py` verifies and provisions catalog-pinned tools. `macloader/dependencies/` resolves a catalog DAG, downloads and verifies archives/files, and caches results. The current toolchain catalog has the Linux x86_64 P4 entry but no equivalent Windows record. Separate buttons/commands remain; there is no campaign-level “prepare what is missing” loop. |
| Private SMBIOS identity | Implemented, awkward guided interaction | `macloader/identity/service.py` stores and validates private identities and references. The TUI asks for filenames or a long magic phrase. The guided surface should offer only “Generate a new private identity” or “Reuse the existing identity for this installation,” without values or paths. |
| EFI generation and validation | Implemented as a lower-level operation | `macloader/build/efi.py` assembles a reviewed profile using configuration, verified dependencies/toolchain, ACPI and identity, then runs structural checks and matching `ocvalidate` and writes a redacted manifest. `WorkflowService.build_efi_preview` can resolve/fetch dependencies but asks for an output path and is not tied to a resumable campaign artifact graph. |
| Recovery | Exact qualification implemented; current exact target externally blocked | `macloader/recovery/{service,discovery,acquirer}.py` and `macloader/domain/recovery.py` bind a strict Recovery lock to target/build/config/EFI evidence and verify Apple-signed chunklists and image chunks. The local runbook/status says Apple discovery returned HTTPS 405 and `AP` is only a product identifier, not proof of build 24A335. No exact Recovery bundle is available. Do not weaken the existing lock to make progress. |
| Prototype Recovery smoke route | Absent | No separate non-qualifying artifact/mode exists. It must not manufacture a `RecoveryLock` claiming 24A335. Any smoke route must retain “build unverified,” preserve the requested target, and prohibit install/erase actions. |
| Removable media | Guarded software writer exists; physical qualification absent | `macloader/removable/{writer,adapters}.py` uses immutable write plans, exact plan-bound expiring confirmation, re-enumeration, invalidation, readback, and lifecycle safeguards. Disposable-image tests exist. ADR-007 in `docs/DECISIONS.md` keeps Windows physical qualification first and Linux second. Windows requires a qualified backend; Linux is not production-advertised. No guided selection/write path is qualified. |
| CLI/TUI | Engineering controls exist; guided journey absent | `macloader/ui/cli.py` exposes config, evidence, identity, toolchain, dependency, build, Recovery and USB commands. `macloader/ui/tui.py` exposes internal IDs, paths and manual controls. Both generally call shared services, but no top-level campaign service or stage model exists. |
| Failure UX | Partially implemented | Structured exceptions and internal error codes exist, but some user-facing handlers show exception details/types as primary text. There is no campaign-level mapping from stable error codes to concise “what failed / what was preserved / what happens next” messages. |
| Fixtures and acceptance evidence | Generic sanitized fixtures exist; exact candidate fixture absent | `tests/fixtures/t480s/t480s_baseline.json` represents 20L7 / BIOS 1.53; `t480s_touchscreen.json` represents 20L8 / BIOS 1.53. Both are not the exact 20L8/1.62 candidate and are not physical evidence. No live sanitized snapshot is available in this workspace. |
| CI | Current run is not green | Hosted Actions run 21 for `a93705e` passed Ubuntu/Python 3.11 and wheel/sdist smoke on both OSes; Ubuntu/Python 3.14 ran 472 passed/1 skipped but failed the 79% coverage threshold at 78.47%; Windows/Python 3.11 and 3.14 each had 11 platform failures among 460 passing tests and coverage below 79%. See section J. |

### Current product-readiness conclusion

The repository is a substantial engineering workbench, not yet an autoloader. The lower-level build and safety services should remain. Product-level work is primarily orchestration, reliable observation reconciliation, provider-backed evidence capture, a real USB-map build input, and guided UX. Current local status and first-install runbook changes are newer than their committed versions and were treated as authoritative for this audit.

## C. Delta matrix

| Requirement | Existing capability | Gap | Proposed change | Main files | Tests | Risk | Human input removed |
|---|---|---|---|---|---|---|---|
| One exact reference campaign | Several reviewed YAML records | Exact selection is duplicated; no campaign registry | Add a versioned campaign record that references model, BIOS, target, profile, config/evidence policy, support state and route policy IDs | `database/loader.py`, `database/schema.py`, new `database/data/campaigns/t480s-20l8-sequoia-15.0.yaml`; affected service defaults | Loader consistency, duplicate campaign, target/profile drift | Drift between linked records | Manual target/profile/policy selection |
| Automatic machine recognition | Linux/Windows probes | Missing panel evidence and some codec/TB detail; no confidence/conflict reconciler | Extend providers; normalize observations; reconcile confidence and provenance into existing configuration observation/confirmation fields | `detection/{linux,windows,normalize}.py`, `domain/{hardware,configuration}.py`, `configuration/service.py` | Provider probes, confidence thresholds, conflict and unknown paths | False certainty, especially “non-touch” from absent data | Typing machine type, BIOS, GPU, audio, Wi-Fi, SSD, panel facts when reliably observable |
| Automatic session creation/resume | Private configuration store and snapshots | UUID manually created/resumed; no machine binding | Add private `AutoloaderSessionStore` and opaque keyed machine binding; auto-create/resume matching campaign session | New `autoloader/` service/store; `configuration/store.py`; workspace layout | Fresh, resume, wrong machine, collision, stale snapshot, redaction | Leaking or confusing machine identity; stale draft reuse | `config new`, UUID copying and manual resume |
| Automatic policy selection and acceptance | P4 profile and digest-bound acknowledgements | Defaults and profile selection are split; raw warning/rule input | Campaign matcher chooses exact policy; guided review shows detected facts; one action records required bound acknowledgements | `configuration/service.py`, campaign registry, `ui/{cli,tui}.py` | Default YAML→plist layout 86, acknowledgement invalidation and stale digest | Acknowledgement bound before final config | Model, target, graphics/audio/network/input option IDs and warning strings |
| ACPI collection | Strict importer/validator and private storage | User supplies directory; no Linux/Windows capture provider | Add `AcpiCaptureProvider`; Linux read-only sysfs capture with narrow elevation; Windows pinned verified ACPICA tool and owned temp paths; import into existing evidence path | New `evidence/acpi_capture.py` or `acpi/providers.py`; `build/acpi.py`; `workflow/service.py`; toolchain catalog | Exact table set; malformed/duplicate/changed table; wrong BIOS/snapshot; permissions/cancel | Privilege boundary, incomplete/unstable table capture | ACPI path, file selection and evidence JSON manipulation |
| USB physical wizard | Evidence session validates records | No device event/correlation provider; user imports JSON/logical IDs; evidence does not generate a map | Add event provider and guided physical action sequence; preserve unresolved mappings; convert accepted observations into explicit generated map/build input | New USB provider/service; `evidence/usb.py`; `build/config.py`, `build/efi.py`; TUI | Insert/remove, companion routes, wrong device, orientation, unresolved, resume; generated-map tests | Incorrect physical/logical association could make ports unavailable or unsafe | JSON, controller IDs, SS/HS route names and speed strings |
| Automatic tools/dependencies/build | Verified provisioning, resolver/cache, EFI builder | Separate actions, arbitrary output path, missing Windows catalog | Campaign auto-run for safe acquire/build steps; private versioned workspace; Windows toolchain entries before Windows guided claim | `toolchain/loader.py`, `dependencies/*`, `workflow/service.py`, `build/efi.py`, catalogs | Missing/cached/corrupt tool; interrupted fetch; invalid identity; rebuild on changed input | Supply-chain drift and stale artifact reuse | Install/resolve/fetch/verify commands, EFI output path and manifest path |
| Private identity decision | Safe private identity service | Filename and magic phrase exposed | Present Generate/Re-use checkpoint; keep identity values/paths out of UI/log/export | `identity/service.py`, `ui/tui.py`, `autoloader/` | New/reuse, invalid identity, redaction, config change invalidation | Accidental identity replacement | File names and macserial details |
| Qualification vs smoke Recovery | Exact target lock and Apple chunk verification | Exact 24A335 unresolved; no safe smoke record | Keep existing qualification path strict; add separate smoke artifact with signed chunklist and chunk hashes, unknown build, explicit mode selection and capability limits | `domain/recovery.py`, `recovery/service.py`, new smoke policy/record and campaign state | Exact block; smoke valid/invalid signature; no false build; target unchanged; no install authorization | Mistaking Apple-signed payload for exact build | Recovery lock path and low-level acquisition fields; conditional mode decision remains |
| Media preparation | Strong guarded writer and disposable tests | No physically qualified backend or guided selection | Preserve Windows-first ADR; add readable device qualification/confirm flow only after backend qualification; exact re-enumeration + full readback | `removable/{writer,adapters}.py`, `workflow/service.py`, TUI | Preserve destructive regressions; exact confirmations, invalidation/readback | Wrong target; physical qualification gap | Device IDs/byte counts; keep target choice and destructive approval |
| First boot and result capture | Runbook describes picker/Recovery only | No guided checkpoint state/result records | Campaign-generated F12/USB instructions and step-by-step smoke result capture; never auto-promote support | New autoloader session/checkpoint model; `docs/HARDWARE_ACCEPTANCE.md`, runbook | Checkpoint pass/fail/not tested; no install transition | Misreading picker as successful Recovery/support | Manual recall/transcription of stage results |
| CI and release gate | Linux/Windows × Python 3.11/3.14; coverage; mypy; package build | Linux-only assumptions run on Windows; 3.14 Linux below coverage | Correct platform boundaries and Windows toolchain; all-platform functional/type/package gates; canonical coverage gate on primary Linux/Python 3.11 with no lowered threshold | `.github/workflows/ci.yml`, platform tests/adapters, toolchain catalog | Matrix contract, platform-specific service tests, coverage, mypy, wheel/sdist | Hidden regressions if platform tests are broadly skipped | Owner manually diagnosing routine CI failures |
| Upstream maintenance | Version-pinned catalogs | No proposal-only update workflow | Later scheduled checker creates candidate catalog change/PR after full dependency, EFI, validator and CI validation; no auto-merge | future `.github/workflows/`, catalog tooling and docs | Fixture build, hash verification, no auto-merge | Supply-chain compromise or profile drift | Routine release polling and repetitive catalog edits |

## D. Proposed architecture

### D.1 Layers and control flow

~~~text
LinuxHardwareProvider / WindowsHardwareProvider
                    │
                    ▼
             HardwareSnapshot
                    │ sanitize + field provenance/confidence
                    ▼
         ObservationReconciler
         ├─ accept high-confidence facts
         ├─ retain unknown/conflicting status
         └─ update existing observations/confirmations
                    │
                    ▼
         ReferenceCampaignMatcher
         ├─ exact machine + BIOS + required topology
         └─ no match / ambiguous / candidate match
                    │
                    ▼
 AutoloaderSessionStore (private, resumable, machine-bound)
                    │
                    ▼
 AutoloaderService.advance_until_blocked()
         ├─ computes structured NextAction
         ├─ runs idempotent safe operations
         ├─ persists provenance and stage result
         └─ stops at human checkpoint or blocker
                    │
                    ▼
 Existing WorkflowService / ConfigurationService / Orchestrator
         ├─ ACPI evidence and private identity
         ├─ toolchain and dependency services
         ├─ BuildPlan → EfiBuilder → validators
         ├─ Recovery qualification or smoke service
         └─ removable-device and guarded writer services
                    │
                    ▼
       Guided CLI/TUI       Engineering CLI/TUI
       render NextAction    expose detailed operations
       submit intent        same semantic services
~~~

`AutoloaderService` owns campaign progression, not lower-level implementation. `WorkflowService`, `ConfigurationService`, `Orchestrator`, compatibility matching, dependency resolution, identity, EFI build/validation, Recovery acquisition and removable writer remain the implementation authorities for their domains. The UI must not duplicate the state machine or infer prerequisites from screens.

### D.2 New semantic contracts

Add a small campaign package, for example `macloader/autoloader/`:

- `CampaignPolicy`: validated references to existing policy records, eligibility constraints, required capabilities/evidence, workflow mode policy and first-install route reference.
- `AutoloaderSession`: private record connecting campaign ID/digest, configuration UUID, opaque machine binding, sanitized snapshot references/digests, action history, artifact references, Recovery mode, media evidence and first-boot checkpoint results.
- `NextAction`: stable action type, category (`automatic`, `human`, `blocked`, `complete`), concise user message, optional human intent choices, internal handler, dependency digest/idempotency key, and detailed diagnostic reference. The guided surface receives no raw rule IDs, evidence paths or serials.
- `advance_until_blocked()`: recompute current prerequisites; perform safe automatic work in dependency order; persist each completed result atomically; return only when the next action requires human input, privilege/external resolution, an unresolved conflict, a physical action, a destructive confirmation, or the first-boot endpoint is reached.
- Provider interfaces for ACPI capture and USB hotplug/route observations. Provider output must use existing evidence types or narrowly extend them, not create parallel configuration facts.

The session is private and can be rebuilt from durable evidence/artifacts after restart. Every automatic operation should be idempotent against an input digest; interrupted acquisition resumes through existing verified partial/cache mechanisms where available. Session state stores references and digests, not private SMBIOS values or raw ACPI in a public record.

### D.3 One campaign record, linked policy objects

Introduce one authoritative reference campaign, likely `macloader/database/data/campaigns/t480s-20l8-sequoia-15.0.yaml`. The record should bind:

- campaign ID and version;
- existing model ID;
- exact machine type 20L8 and BIOS vendor/version binding N22ET85W 1.62;
- existing exact macOS target record for 15.0 / 24A335;
- reviewed P4 EFI profile ID;
- existing configuration policy ID and evidence policy ID;
- allowed component identity predicates and required capability references;
- experimental/qualification state;
- required evidence kinds and USB scope;
- first-install USB route policy reference and F12 instruction key;
- exact Recovery qualification mode and separately defined smoke-mode eligibility.

The campaign record must not copy graphics platform IDs, audio layout, kext versions, ACPI patch contents or dependency pins from the profile/catalogs. It links them and validates that the referenced records agree. Campaign loading fails closed on a dangling reference or profile/target/config mismatch. A later T480s BIOS or a 20L7, T480 or X280 campaign gets a distinct record and reviewed links, not additional machine-specific branches.

The effective audio path must remain unambiguous: campaign selects P4; P4 and configuration default use layout 86; `ConfigurationService` exposes that effective choice; `SchemaDrivenConfigGenerator` writes 86 into the plist. The new regression test covers the complete default path, not a second audio-selection mechanism.

### D.4 Observation reconciliation and scoped invalidation

Use the existing `HardwareObservation` and `HardwareConfirmation` fields on `UserConfiguration`. Each observation should carry a normalized fact key, value, source, confidence, observed time, snapshot digest, and relevant machine/BIOS provenance. Reconciliation rules:

1. A high-confidence observation with no conflicting source becomes accepted automatically and can satisfy a campaign fact.
2. An unknown or low-confidence observation remains unknown. Ask only if that fact is a required campaign discriminator or safety input.
3. Conflicting high-confidence observations stop policy selection and show the two human-readable facts and their sources.
4. A campaign match requires exact identifiers and required component predicates. An absent field cannot be treated as a negative finding.
5. A changed fact invalidates evidence/artifacts whose dependency scope includes that fact. A changed BIOS invalidates BIOS-bound ACPI and affected EFI; a changed storage identity need not invalidate audio observations or unrelated dependency cache.

Today a whole-snapshot digest is used as a broad stale check. Keep it for provenance, but add a scoped input digest/invalidation key to existing evidence/artifact bindings where needed. Migrate older schema-1 records conservatively: if their scope cannot be proven, mark them stale and recollect/revalidate rather than assuming they remain valid. Do not persist public serial numbers, UUIDs, MACs, storage serials or raw captures in observations.

### D.5 Privacy-preserving session binding

Create a private per-user machine-binding key in the protected workspace. Where the OS exposes stable machine serial/UUID, derive an opaque keyed HMAC from normalized private source material; never use a raw value or unhashed public digest as a session lookup key. Keep the HMAC and its key in private storage, omit them from redacted exports/logs/status views, and never treat the binding as proof of support. Require structural corroboration from model, machine type, BIOS and component topology before auto-resuming.

If no private stable identifier is available, or two sessions plausibly match, stop with a choice among human-readable campaign sessions; do not ask the user to type a configuration UUID. Wrong BIOS or changed critical topology must not silently resume a stale session. A new session can still be created automatically for a clear exact candidate. Sanitized snapshots are stored privately; exports remain sanitized.

### D.6 Provenance and failure reporting

Every action record stores campaign/policy digest, relevant configuration revision, input snapshot/evidence digests, tool/dependency lock digests, output artifact digest, started/completed/cancelled/failed status and a stable internal error code. UI summaries should answer what failed, whether a destructive operation happened, what MacLoader preserved or can resume, and the next physical or software action.

Example mapping: `EVIDENCE_SOURCE_MISSING` becomes “Firmware-table evidence is missing. MacLoader can collect it now.” Detailed exception and paths remain behind Engineering / diagnostics. A failed build must never be reported as media-ready.

## E. Autoloader state machine

The engine should derive the current stage from valid inputs and durable outputs each time; persisted stage is a progress hint, not authority. Upstream digest changes invalidate dependent state and recompute the next action. A stage transition is committed only after the operation result is durable.

| State | Entry condition | Automatic action | Success transition | Failure/block transition | Invalidation trigger | Human interaction |
|---|---|---|---|---|---|---|
| 0. Detect | Guided launch or resume | Probe OS provider; normalize and sanitize; record snapshot provenance | 1 | Provider unavailable → actionable blocker; partial snapshot may continue if match is still safe | New probe | OS privilege prompt only if provider requires it |
| 1. Match campaign | Snapshot exists | Match model/machine type/BIOS and required components | 2 for one exact match | Wrong/unsupported → no T480s policy; conflicting/ambiguous → focused question; never inherit from family name alone | Machine type, BIOS, required topology change | Only for genuine conflicting evidence |
| 2. Locate/create session | Exact candidate match | HMAC-bound lookup; resume compatible session or atomically create one; bind sanitized snapshot | 3 | Ambiguous prior sessions → offer readable choices; storage error → blocker | Binding key/critical facts/campaign digest | Choice only if session match ambiguous |
| 3. Reconcile observations | Session and snapshot bound | Populate existing observation/confirmation contracts; calculate scoped changes | 4 | Required fact unknown/conflicting → focused prompt; unsupported component → stop | New hardware snapshot | Only unresolved required fact |
| 4. Select effective policy | Observations satisfy campaign | Select target, profile, config policy, evidence policy and defaults; validate linked-record consistency | 5 | Policy drift → fail closed; no arbitrary fallback | Campaign/config/profile digest | None for exact match |
| 5. Collect ACPI evidence | Machine/BIOS binding known; no current valid capture | Invoke host AcpiCaptureProvider; Linux sysfs read or Windows pinned collector; privately store/hash/validate; attach existing evidence | 6 | Permission/tool/capture/table error → focused action and resume point | BIOS or relevant firmware table/snapshot binding change | OS elevation prompt; one tool acquisition instruction only if trusted provisioning fails |
| 6. Collect USB physical evidence | Campaign requires physical map | Wizard asks physical movement; provider detects insertion/removal, device/controller/logical route/speed; stores existing UsbEvidenceSession records | 7 when required scope resolved; unresolved connectors remain marked | Correlation not provable → retain unresolved and ask focused retry or block required map | Controller, topology, snapshot, provider data | Physical moves; no logical IDs or JSON |
| 7. Prepare trusted tools | Toolchain requirements known | Verify installed tools; provision missing catalog-pinned host artifacts and hashes | 8 | Unsupported host/artifact/hash failure → actionable external blocker | Tool catalog digest / host platform | Only if catalog cannot safely provision |
| 8. Resolve/acquire dependencies | Toolchain verified | Resolve capability closure, reuse valid cache, download and verify missing items, persist lock | 9 | Network/hash/archive/licensing issue → preserve partial state and present concise fix | Dependency catalog/profile change | Only external access/credential issue; no dependency IDs |
| 9. Private identity decision | Config/evidence prerequisites known; no valid bound identity | Offer Generate new or Reuse existing; call IdentityService; bind private ref and validation digest | 10 | Invalid/cancelled identity → remain here; never generate invisibly | Identity reference or config binding change | One deliberate choice |
| 10. Experimental policy review | Effective config and required evidence stable | Render exact detected machine/BIOS/graphics/audio/network/target/profile and experimental status; on acceptance call digest-bound acknowledgement service for required rules | 11 | Decline → pause; changed config/policy invalidates acknowledgement and returns here | Any acknowledged config/policy/evidence digest change | One acceptance action |
| 11. Build and validate EFI | Current config, identity, ACPI, tools and dependency lock valid | Build in private versioned campaign workspace; structural validation; matching ocvalidate; redacted manifest; persist artifact relationship | 12 | Stale/mismatch/validator failure → preserve diagnostics and invalidate artifact; return to needed state | Inputs in builder binding change | None |
| 12. Resolve Recovery trust mode | EFI artifact valid | Attempt exact target discovery/qualification first | 13 exact if authenticated exact build; smoke branch only if selected and policy permits | Exact discovery ambiguous/405 → qualification blocked; never lock as 24A335 | Requested target/policy/protocol version | Conditional explicit choice to enter smoke-only mode |
| 13. Acquire/verify Recovery | Selected mode and valid EFI | Exact path uses strict service/lock. Smoke path uses separate artifact record, signed chunklist and chunk hashes, HTTPS asset policy, build unknown | 14 | Signature/hash/host/size mismatch → reject; exact mode remains blocked | Recovery source, target, EFI binding or record | None beyond mode decision |
| 14. Qualify media target | Recovery and EFI inputs valid; writer backend qualified | Enumerate, filter unsafe/internal candidates, show model/capacity/stable human identity, create exact write plan | 15 after selection | Writer unqualified/no unique safe candidate/stale identity → block | Enumeration and plan digest | Select device if multiple eligible |
| 15. Confirm/write/read back | Fresh enumeration still matches exact plan | Re-enumerate, verify identity, require fresh destructive confirmation, write, full readback, invalidate failure, safe eject, persist evidence | 16 on complete verification | Identity drift, cancel, write/readback failure → stop; preserve invalidation marker and diagnostics | Source/media/input digest or timeout | One explicit destructive approval for exact USB |
| 16. First-boot instructions | Verified media and campaign route available | Render route, F12, picker, Recovery and no-install instructions | 17 | Missing physical route proof → return to USB evidence | Route evidence or media artifact change | Physical insertion and F12/boot actions |
| 17. Smoke-test checkpoints | User reports test progress | Record picker, Recovery utilities, no disk action, shutdown/reboot, existing OS bootable individually | 18 when result recorded; outcome may be pass/fail/not-tested | Failure stores stopping point; does not retry automatically or promote support | New attempt creates new record | Physical observation/checkpoint confirmation |
| 18. v1 ready/complete | Media prep and smoke result recorded, or safe stop after media prep | Show endpoint, artifact provenance and acceptance status | Terminal for campaign version | Never transitions to macOS install automatically | New campaign revision starts new session revision | None |
| 19. Later installation checkpoint | Separate future install campaign explicitly invoked | Identify internal disk using multiple stable properties and fresh state; request new destructive confirmation | Only future supervised install may proceed | Any ambiguity blocks | Every new boot/target enumeration | Not part of v1; never implied by states 17/18 |

`advance_until_blocked()` must not execute physical or deliberate-choice states without the corresponding human action. Safe deterministic software actions can run in a batch and report progress. Every retry uses the same input digest/idempotency key; changed inputs create a new action attempt.

## F. Human interaction map and budget

The target for an exact, healthy T480s remains approximately three deliberate in-product decisions, excluding OS privilege prompts and physical USB movements.

| Human action | Classification | Removable? | Reason / UX |
|---|---|---|---|
| Accept experimental P4 prototype configuration | Safety-required | No | Configuration is not physically accepted. Show detected hardware and target; one action records currently required acknowledgements against final config/policy digest. |
| Choose Generate new private identity or Reuse identity for this installation | Safety/privacy-required | No | Both affect persistent private identity. Hide serial/MLB/UUID/ROM, filenames and tool arguments. |
| Move test USB among requested physical ports and orientations | Unavoidable physical evidence | No | Software cannot know which chassis opening was used. User does not type labels, controller IDs, speeds or routes. |
| Approve narrow OS elevation for ACPI capture if needed | OS-required | Not safely removable on Linux | Firmware tables are root-only through Linux ACPI sysfs. Request least privilege; do not relaunch the application as administrator. |
| Choose exact Recovery qualification or non-qualifying smoke-test mode if 24A335 cannot be proven | Trust/scope-required and conditional | No while exact build is unproven | Smoke mode explicitly accepts unknown build relation. It does not change target or authorize installation. |
| Select removable drive if multiple safe candidates exist | Physical-target decision | No | Show model, capacity and stable identity; never use enumeration number as identity. Even one candidate still requires write confirmation. |
| Confirm destructive write of exact USB | Safety-required | No | Fresh re-enumeration and exact plan confirmation immediately precede write. |
| Insert media, use F12, choose USB/OpenCore/Recovery entries, observe checkpoints, shut down/reboot and verify existing OS | Unavoidable physical actions | No | The v1 goal is supervised smoke testing. MacLoader records outcome; it cannot operate firmware UI or infer success from validator output. |
| Resolve genuine conflicting/unknown required hardware fact | Conditional focused question | Only if a better provider can reliably observe it | Never ask for facts already available at high confidence. |
| Obtain trusted tool or network access if provisioning fails | Conditional external blocker | Sometimes | Normal path provisions automatically; intervention only for source, network or catalog failure. |

No guided action should request a configuration UUID, option/rule ID, warning text, evidence JSON/ACPI path, dependency ID, Recovery lock, EFI manifest, output path, SMBIOS filename, byte count, raw controller ID, logical USB route, or arbitrary plist edit.

## G. Data and schema changes

Keep the schema surface small and reuse current domain contracts.

1. **Campaign policy file and loader.** Add a campaign registry to Database with strict reference validation. It links to existing model/release/profile/configuration/evidence/toolchain/dependency/recovery records. Include schema version, campaign ID/revision, eligibility predicates, support state, required evidence kinds, allowed workflow modes and instruction keys. Do not repeat component tuning.
2. **Autoloader session store.** Add a private workspace-owned session record. Fields: session ID; campaign ID/revision/digest; opaque private machine binding; configuration UUID/revision; sanitized snapshot reference/digest; action records; active NextAction; artifact references/digests; Recovery mode/status; media evidence reference; first-boot checkpoint results. Atomic compare-and-swap and permissions should follow ConfigurationStore patterns.
3. **NextAction contract.** Use a small enum and typed payload; separate display copy from internal handler/error code. No arbitrary command execution, free-form paths or UI-owned progression.
4. **Observation provenance.** Extend current HardwareObservation only for missing source/confidence/scope metadata. Continue using UserConfiguration.observations and .confirmations; do not add another observations database.
5. **Scoped evidence invalidation.** Extend current EvidenceRecord or binding metadata with input-scope digest and invalidation categories. Retain the full snapshot digest for audit. Unknown old scope becomes stale and requires recollection/revalidation. Do not invalidate unrelated artifacts solely because another component changed.
6. **Capture provider outputs.** ACPI output becomes current AcpiEvidence/private artifact metadata. USB wizard emits existing UsbPortObservation/UsbEvidenceSession. Add only the generated USB map artifact/binding needed by the build; the evidence session remains the source of physical assertions.
7. **Recovery smoke record.** Add a distinct prototype Recovery record with requested target, resolved build unknown, source/tool version, signed-chunklist result, asset/chunk digests, timestamps, and purpose restrictions. Do not encode it as RecoveryLock, claim build 24A335, or mark exact trust.
8. **Host toolchain catalog.** Add OS/architecture-specific pinned OpenCore utilities and ACPICA entries where supported; include release, asset/member path, license notice requirement and SHA-256. Do not claim Windows guided readiness until Windows validator/iASL provisioning is exercised.
9. **Migrations and redaction.** Version new session/evidence data independently where practical. Existing configuration schema v1 migrations preserve old IDs/records, mark unverifiable evidence stale, retain acknowledgements only when digest matches, and keep machine bindings, raw observations, private references and identity values out of public exports.

## H. UI responsibilities

### Guided Autoloader (default)

One launch entry point, such as `macloader autoload`, and the TUI default. It asks AutoloaderService for progress and NextAction; it does not compute its own steps. Show detected laptop/campaign match in readable language, campaign progress/current automatic activity, one next physical action or deliberate decision, concise blocker and preservation status, and clear experimental/unqualified status.

Use plain display names. Show paths, detailed errors and internal policy only after the user opens diagnostics. Never expose generated identity values. The service should continue after each action and run newly unblocked safe steps automatically.

### Engineering / Advanced

Keep current CLI/TUI capabilities for config IDs, option selection, policy acknowledgements, evidence import/export, dependency operations, explicit paths, manifest inspection, Recovery internals, debug output and test/support workflows. Do not remove engineering controls. Both surfaces use WorkflowService, ConfigurationService, Recovery/build/media services and the same campaign engine.

### Failure copy

The primary message names the problem, destructive status, preserved state and next action. Example: “Firmware-table evidence is missing. MacLoader can collect it now.” Keep stable codes such as EVIDENCE_SOURCE_MISSING in diagnostics and tests. Never put raw exception dumps in the guided main view.

## I. Recovery mode and trust boundaries

### I.1 Qualification mode

The v1 qualification target remains exactly Sequoia 15.0 / build 24A335. Reuse the existing exact RecoveryTarget, RecoveryService, binding, lock and verification semantics. A product ID (AP) or Apple-signed payload alone does not authenticate its relationship to build 24A335. If discovery cannot prove the exact build, qualification remains blocked. The requested target does not silently change and no exact Recovery lock is created.

This matches current local evidence: the recorded Apple discovery attempt returned HTTP 405; AP did not prove the requested build; no Recovery image/chunklist was downloaded. Discovery can be retried, but historical availability is not assumed.

### I.2 Prototype smoke-test mode

Add only as a separate, opt-in path after review of the pinned official OpenCore client/protocol. It may accept an Apple Recovery payload whose signed chunklist and all image chunks verify while the exact macOS build remains unknown. Its record says:

- requested target remains 15.0 / 24A335;
- resolved build is **unknown / unproven**;
- Apple chunklist signature and payload integrity are verified (or acquisition is rejected);
- mode is **prototype smoke test only, not qualification**;
- permitted outcomes are OpenCore picker, Recovery boot, and basic environment/network reachability;
- it cannot satisfy qualification Recovery prerequisites, promote support, authorize “Install macOS,” or authorize internal-disk operations.

Use a distinct smoke artifact type and UI labeling. Do not synthesize an exact Recovery product or lock. Recovery utilities may expose erase/install controls; user instructions stop at the utilities screen. The service boundary should not hand a smoke-only record to an installer-media or install authorization path that expects qualified Recovery. If acquisition cannot maintain a trusted HTTPS asset allowlist, authenticate the chunklist, and preserve source/protocol evidence, keep smoke mode blocked.

### I.3 Current official-tool research and versioning

As of the audit date, the official [OpenCorePkg 1.0.8 release](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.8), dated 2026-09-27, is newer than the repository’s frozen 1.0.7 qualification toolchain and specifically includes a Windows macrecovery fix/launch helper. The [1.0.8 macrecovery README](https://github.com/acidanthera/OpenCorePkg/blob/1.0.8/Utilities/macrecovery/README.md) documents the client; the [pinned client source](https://github.com/acidanthera/OpenCorePkg/blob/1.0.8/Utilities/macrecovery/macrecovery.py) still uses Apple’s legacy HTTP metadata request and treats AP as an opaque product identifier. It verifies signed chunklists and image hashes but does not provide exact build binding. Therefore:

- keep OpenCore 1.0.7 frozen for the current EFI qualification profile;
- evaluate 1.0.8 only as a separate, versioned Recovery-tooling migration or candidate campaign;
- pin/hash its source or executable and review protocol/redirect/asset-host boundaries;
- do not upgrade the campaign’s EFI baseline as a side effect of the UI/orchestrator refactor.

### I.4 ACPI collection trust boundary

Linux kernel ACPI sysfs exposes tables under `/sys/firmware/acpi/tables` and dynamic tables, with access restricted to root ([kernel sysfs source](https://github.com/torvalds/linux/blob/master/drivers/acpi/sysfs.c) sets table attributes to mode 0400; the [acpidump manual](https://github.com/torvalds/linux/blob/master/tools/power/acpi/man/acpidump.8) documents these paths). Implement capture through a narrowly scoped read-only privileged helper or approved elevation; never restart all of MacLoader as root. Capture into private temporary storage, compare machine/BIOS/snapshot before and after, validate exact required names/count/header/checksum, hash, then publish only the private evidence reference.

For Windows, the existing runbook’s ACPICA `acpidump.exe` hash is `a0095a57521378c290d030db7ad196a27de2fcc770dd0347104ff49d19d792e0`. The official [ACPICA 20260408 release](https://github.com/open-acpica/acpica/releases/tag/20260408) contains the tool; its source licensing permits binary redistribution subject to its disclaimer/notice. P5 should verify the exact release member, hash, and license notice before catalog provisioning. If review prevents bundling, MacLoader should detect/validate the one expected tool, provide one acquisition instruction, then own invocation and paths.

Raw ACPI tables never enter Git, fixtures, redacted exports or ordinary logs.

## J. CI and test plan

### J.1 Current hosted state

The latest inspected hosted Actions run is run 21 for commit a93705e ([run details](https://github.com/abharrison1995-droid/Libre_Core-MacLoader/actions/runs/37001038077)). It is not green:

- Ubuntu/Python 3.11 test/type-check job passed.
- Ubuntu and Windows wheel/sdist package smoke jobs passed.
- Ubuntu/Python 3.14 ran 472 passing tests and one skip, then failed the existing 79% branch-coverage threshold at 78.47%; type checking did not run after the test step failed.
- Windows/Python 3.11 and 3.14 each ran 460 passing tests and 11 failures, with coverage 77.13% and 76.60%; the test-step failure prevented later type checking.

Windows failures are not a reason to broadly skip Windows: Linux ocvalidate is executed on Windows; ACPI cancellation tests use POSIX os.getpgid; one cancellation test assumes a Linux iASL executable; identity permission tests assume POSIX chmod semantics; removable tests assume Linux adapter/os.geteuid/mount behavior. Correct the boundary:

1. Keep portable domain/contract, parser, policy, archive, privacy and safety tests cross-platform.
2. Put actual Linux process-group/sysfs/removable integration behavior under Linux-specific tests; inject OS/process/adapters so portable semantics still receive deterministic cross-platform unit coverage.
3. Add Windows-specific tests for Windows toolchain, process cancellation, ACL/private-store behavior and Windows removable adapter contracts.
4. Do not compare a Linux validator binary to Windows behavior. Add pinned Windows OpenCore validator/toolchain before claiming Windows guided build support.
5. Keep all functional tests and mypy required on all claimed OS/Python cells. Package build/install smoke stays on Ubuntu and Windows.
6. Keep a canonical branch coverage gate of at least the existing 79% on primary Linux/Python 3.11; upload/track coverage on every matrix cell and investigate meaningful drops. This avoids false failures from platform-asymmetric branches without reducing the threshold or hiding failing tests. Add coverage for new code and preserve or improve effective coverage.

P0 should use the CI matrix’s actual reports as evidence. A commit is “known green” only after all required test/type/package gates on hosted CI pass.

### J.2 Required regression groups

Add tests with the phase that implements each behavior. This planning pass did not run tests.

**Autoloader/session:** fresh exact 20L8/1.62 match; auto-create; restart/resume; wrong machine; wrong BIOS; component mismatch; ambiguous candidates; stale snapshot; targeted evidence invalidation; unrelated component change preserves unrelated evidence; interrupted dependency fetch; valid/invalid identity; strict Recovery blocked; smoke mode separated; incomplete/complete USB evidence; writer unavailable; verified media; first-boot-ready endpoint.

**Observation reconciliation:** high-confidence accepted; source conflict; unknown required fact prompts; unknown optional fact does not prompt; BIOS update invalidates ACPI/downstream EFI; storage identity change invalidates only dependent records; no facts inferred from missing probes; sanitize before persistence.

**Hardware providers:** synthetic Linux sysfs/DRM EDID/ACPI fixtures and Windows CIM/PnP/monitor fixtures cover manufacturer, exact product, BIOS, CPU, iGPU/dGPU, codec/subsystem, Wi-Fi, Bluetooth, Ethernet, storage model, input topology, USB controller and display/touch confidence. No fixture contains machine serial, UUID, MAC or storage serial.

**ACPI capture:** exact DSDT plus expected SSDT set; malformed/checksum-invalid table; missing/duplicate table; mutation during capture; wrong BIOS/snapshot; privilege failure; cancellation; private publication/redaction; no raw table in logs.

**USB wizard/map:** insertion/removal; wrong test device; USB 2/3 companion mapping only where proven; duplicate observation; each USB-C orientation; unresolved correlation persists; cancellation/resume; generated port map derives solely from accepted evidence and is included in build/manifest; required route exists; no inferred route from port order.

**Guided UX:** never request config ID, rule ID/warning text, evidence JSON/ACPI path when direct capture succeeds, logical USB port/controller, dependency ID, Recovery lock path, EFI output/manifest path, identity filename or arbitrary plist setting. CLI and TUI use same semantic service.

**Recovery:** exact 24A335 only locks on exact authenticated product/build relation; HTTP 405/opaque AP remains ambiguous; smoke path requires explicit mode; signed chunklist/image failure rejects; asset-host/TLS policy; no version substitution or target mutation; smoke record cannot pass qualification or install guards.

**Safety/privacy:** retain and extend current destructive-target, re-enumeration, source digest, readback, cancellation and invalidation tests; private identity redaction; evidence/config digest binding; export/log/screenshot/status sanitization; no fixture treated as physical evidence.

## K. Dependency-ordered implementation phases

Commands below are proposed for implementation phases, not commands run in this planning pass. Each phase should be a small reviewable change with its own rollback point.

### P0 — Baseline and CI repair

- **Objective:** Establish hosted green commit on supported matrix before hardware campaign.
- **Files:** `.github/workflows/ci.yml`; platform-sensitive tests under `tests/unit/`; `macloader/build/acpi.py`; `macloader/identity/service.py`; `macloader/removable/adapters.py`; `macloader/toolchain/loader.py`; toolchain catalog as Windows support is added.
- **Work:** Reproduce exact hosted failures; split host integration from portable behavior; add injectable process/platform boundaries; repair Windows semantics instead of removing tests; choose canonical 79% Linux/Python 3.11 coverage gate while retaining all matrix functional tests and mypy; maintain package smoke on both OSes. Verify layout-86 correction remains intact.
- **Tests:** Existing failures plus focused Windows process, permission, validator selection and removable contracts; matrix workflow assertions.
- **Docs:** CI policy, supported host/toolchain matrix and known-green commit rule.
- **Commands:** `python -m pip install -e ".[dev]"`; the current CI test command (`python -m pytest -q --cov=macloader --cov-branch --cov-report=term-missing --cov-report=xml:coverage.xml --cov-fail-under=79`); `python -m mypy macloader tests --follow-imports=skip`; `python -m pip install build`; `python -m build --wheel --sdist`; then run `tests/package_smoke.py` against both artifacts outside the checkout. Hosted Actions is final gate.
- **Definition of done:** Required Ubuntu/Windows × Python 3.11/3.14 functional/type checks and package smoke pass; coverage policy explicit and not reduced; no broad platform skip.
- **Rollback boundary:** Revert CI/platform-adapter/test changes independently; no campaign/schema migration has started.
- **Depends on:** None.

### P1 — Reference campaign policy

- **Objective:** Make one versioned record the authoritative composition for exact T480s 20L8/1.62 → P4 → Sequoia 15.0/24A335.
- **Files:** `macloader/database/{loader.py,schema.py}`; new `database/data/campaigns/`; `configuration/t480s.yaml`; P4/evidence/recovery/release records; selector sites in `configuration/service.py`, `workflow/service.py`, `build/config.py`, `build/acpi.py`, `recovery/service.py`.
- **Work:** Add validated references and consistency checks; replace unnecessary service-level literals with campaign lookup; preserve existing policy content. Keep layout-86 default and add default-path integration assertion. Do not build a parallel database of kexts, graphics/audio tuning or ports.
- **Tests:** Dangling reference, mismatched BIOS/target/profile, duplicate eligibility, exact match, nonmatching 20L7/touchscreen do not inherit 20L8/1.62 policy, default YAML → effective config → plist layout 86.
- **Docs:** Campaign record ownership/versioning; update architecture/decision records when implementation lands.
- **Commands:** `python -m pytest -q tests/unit/test_database_loader.py tests/unit/test_configuration_workflow.py tests/unit/test_readiness_repairs.py tests/integration/test_pipeline.py`; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip`.
- **Definition of done:** One campaign policy selects the reviewed composition and fails closed if linked records drift.
- **Rollback boundary:** Campaign loader/data/caller conversion revert without changing existing profile contents or user configuration format.
- **Depends on:** P0.

### P2 — Hardware observations and reconciliation

- **Objective:** Use machine evidence instead of asking the user to type detectable values.
- **Files:** `macloader/detection/{linux.py,windows.py,normalize.py,sanitize.py}`; `domain/{hardware.py,configuration.py}`; `configuration/{service.py,store.py}`.
- **Work:** Populate display/EDID/resolution/internal/touch evidence where sources support it; improve audio codec/subsystem and Thunderbolt/USB probes; define confidence/provenance; wire facts into existing observations/confirmations; preserve unknown/conflicting facts. Implement field-scoped invalidation. “Non-touch” requires positive sufficiently complete evidence, not absence of a probe.
- **Tests:** Provider fixtures and reconciliation cases from J.2; BIOS/affected-only invalidation; sanitizer checks.
- **Docs:** Per-field confidence/source policy and why each observation supports campaign matching.
- **Commands:** `python -m pytest -q tests/unit/test_linux_provider.py tests/unit/test_windows_provider.py tests/unit/test_normalization.py tests/unit/test_sanitization.py tests/unit/test_domain_models.py tests/unit/test_hardware_reconciliation.py`; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip` on Linux and Windows.
- **Definition of done:** High-confidence machine type, BIOS and components select campaign automatically; uncertainty is explicit and no missing fact is fabricated.
- **Rollback boundary:** Provider changes/reconciliation can be disabled independently; existing snapshot/config schema still loads.
- **Depends on:** P0, P1.

### P3 — Persistent session and NextAction engine

- **Objective:** Create/resume a campaign session automatically and make the application choose the next safe action.
- **Files:** New `macloader/autoloader/{service,models,store,matcher}.py`; `configuration/store.py`; `workflow/service.py`; `domain/evidence.py`; `macloader/app.py`; workspace config.
- **Work:** Add private machine-binding HMAC and structural corroboration; create/resume session; typed NextAction; action journal, digest/idempotency keys and restart recovery; `advance_until_blocked`; scoped invalidation. Keep UUID internal to guided mode. Older evidence with unknown scope becomes stale.
- **Tests:** Fresh, resume, wrong machine/BIOS/component, multiple matching sessions, private binding redaction, crash between action and session save, stale and unrelated evidence changes, idempotent retry.
- **Docs:** Session data/privacy/migration format and action semantics.
- **Commands:** `python -m pytest -q tests/unit/test_autoloader.py tests/unit/test_autoloader_store.py tests/unit/test_configuration_workflow.py`; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip`.
- **Definition of done:** Second launch resumes valid campaign without asking for UUID; wrong/ambiguous binding stops safely.
- **Rollback boundary:** New private session format can be removed while original configuration snapshots remain readable.
- **Depends on:** P1, P2.

### P4 — Guided policy acknowledgement and identity checkpoint

- **Objective:** Replace low-level inputs for policy acceptance and SMBIOS identity with two meaningful decisions.
- **Files:** `identity/service.py`; `configuration/service.py`; `ui/{cli.py,tui.py}`; autoloader action handlers.
- **Work:** Campaign chooses target/profile/options. Guided review summarizes facts and experimental status; one acceptance writes existing digest-bound acknowledgements. Identity view offers Generate/Re-use and delegates to IdentityService. Config/policy mutation invalidates acceptance.
- **Tests:** One acceptance binds all current warnings; changed digest needs renewed acceptance; identity generate/reuse/invalid/cancel; no private values or filenames in status/export.
- **Docs:** Guided identity and experimental-profile copy/safety.
- **Commands:** `python -m pytest -q tests/unit/test_cli.py tests/unit/test_tui.py tests/unit/test_configuration_workflow.py tests/unit/test_p4_identity.py`; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip`.
- **Definition of done:** Guided user never enters option/rule/warning IDs or identity path/phrase.
- **Rollback boundary:** Revert guided views while retaining Engineering commands and policy contracts.
- **Depends on:** P3.

### P5 — Direct ACPI collection

- **Objective:** Collect firmware tables from host and attach them to current evidence automatically.
- **Files:** New `macloader/evidence/acpi_capture.py` provider contract/platform providers; `build/acpi.py`; `workflow/service.py`; toolchain catalog/license notice; Windows capture integration.
- **Work:** Linux reads expected ACPI sysfs entries through narrow read-only elevation; Windows uses official pinned ACPICA acpidump.exe or one validated acquisition instruction. Capture privately; compare snapshot/BIOS before and after; validate existing exact table set and strict checks; hash and attach evidence.
- **Tests:** Exact set; malformed/duplicate/missing; mutation; wrong BIOS/snapshot; permission/cancel; tool hash/license metadata; no raw data leakage.
- **Docs:** Privilege boundary, ACPICA provenance/license, fallback if provisioning fails.
- **Commands:** `python -m pytest -q tests/unit/test_p4_acpi.py tests/unit/test_acpi_capture.py tests/unit/test_linux_provider.py tests/unit/test_windows_provider.py`; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip`.
- **Definition of done:** Guided path says “Collect firmware tables” and owns paths; no directory requested when capture succeeds.
- **Rollback boundary:** Disable provider and retain manual import in Engineering.
- **Depends on:** P2, P3.

### P6 — USB physical evidence wizard and map contract

- **Objective:** Ask user to move a test device while MacLoader records proven physical/logical correlation.
- **Files:** New USB host-event/provider and collector service; `evidence/usb.py`; `domain/configuration.py`; TUI; campaign evidence scope.
- **Work:** Monitor insertion/removal and sysfs/PnP route/controller/speed; pair event to wizard step; associate physical label only from instructed action; verify test-device identity; persist resumable session. Preserve uncertainty for USB-C orientation/companion paths. Specify generated map artifact contract for build consumption in P7.
- **Tests:** Insert/remove, wrong device, duplicate, USB2/3 companion only if proven, orientations, unresolved, cancel/resume, provider unavailable, evidence digest binding.
- **Docs:** Physical sequence, minimum campaign map scope, unresolved-port behavior.
- **Commands:** `python -m pytest -q tests/unit/test_usb_evidence.py tests/unit/test_usb_capture.py tests/unit/test_domain_models.py`; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip`. No physical port test runs in software CI.
- **Definition of done:** User performs physical actions only; no JSON/controller/logical-port entry; unresolved route stays unresolved.
- **Rollback boundary:** Disable wizard and retain manual USB evidence import for Engineering.
- **Depends on:** P2, P3.

### P7 — Automatic tools, dependencies, build and actual USB map

- **Objective:** Complete safe software preparation with one campaign advance loop.
- **Files:** `toolchain/loader.py`; `database/data/toolchains/catalog.yaml`; `dependencies/*`; `workflow/service.py`; `build/{config.py,efi.py}`; USB map contract.
- **Work:** Add host-specific verified toolchain entries, including Windows OpenCore validator and ACPICA where supported; auto-verify/provision, resolve, download/cache and build; own private versioned campaign output; bind config, identity, ACPI, dependencies, tools and accepted USB map into build; structural/matching ocvalidate; redacted manifest/session relationship. Rebuild/invalidate relevant changes.
- **Tests:** Missing/cached/corrupt tools, failed/interrupted downloads, offline blocker, identity, stale ACPI, USB map generation/input, deterministic manifest, validator failures, no arbitrary output path.
- **Docs:** Host support/provenance and deterministic build boundary.
- **Commands:** `python -m pytest -q tests/unit/test_dependencies_catalog.py tests/unit/test_dependencies_resolver.py tests/unit/test_dependencies_downloader.py tests/unit/test_p4_toolchain.py tests/unit/test_efi_builder.py tests/integration/test_dependencies_pipeline.py tests/integration/test_pipeline.py`; then full `python -m pytest -q`, `python -m mypy macloader tests --follow-imports=skip` and both package smoke jobs.
- **Definition of done:** Exact campaign auto-prepares safe local prerequisites and produces validated private EFI; generated config includes only evidence-backed USB mapping.
- **Rollback boundary:** Revert campaign automation to existing manual service commands; catalogs/artifacts remain versioned.
- **Depends on:** P0, P1, P3, P5, P6; P4 guided identity/acknowledgement.

### P8 — Recovery qualification and smoke-mode separation

- **Objective:** Preserve strict target behavior while allowing explicitly non-qualifying picker/Recovery test only if verifiable.
- **Files:** `domain/recovery.py`; `recovery/{service,discovery,acquirer}.py`; recovery catalog; campaign policy; guided UI.
- **Work:** Keep qualification lock exact. Review/pin official OpenCore macrecovery (1.0.8 candidate) independently; add distinct smoke artifact and capability boundary; authenticate Apple chunklist, verify chunks, enforce source/asset rules, record build unknown and explicit user choice; never change target.
- **Tests:** Exact 405/opaque AP blocked; exact build locks; smoke signature/source failure rejected; smoke cannot satisfy exact lock/install; no target substitution; restart/resume.
- **Docs:** Trust properties, protocol risks, tool version, conditional mode choice; update research ledger.
- **Commands:** `python -m pytest -q tests/unit/test_recovery_discovery.py tests/unit/test_recovery_acquirer.py tests/unit/test_recovery_smoke.py`; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip`.
- **Definition of done:** Exact mode remains truthful; smoke mode visibly non-qualifying and inaccessible to install authorization. If trust rules cannot be met, smoke remains blocked.
- **Rollback boundary:** Remove smoke route without touching strict qualification.
- **Depends on:** P1, P3, P7.

### P9 — Guided removable media and writer qualification

- **Objective:** Connect existing guardrails to guided flow only after platform writer is physically qualified.
- **Files:** `removable/{writer.py,adapters.py}`; media evidence store; `workflow/service.py`; CLI/TUI; ADR-007.
- **Work:** Keep disposable-image, writer implementation, physical qualification and production writes separate. Preserve Windows-first then Linux ADR order. On a qualified host, hide unsafe/internal targets, show readable/stable properties, let user select, immediately re-enumerate/compare, issue fresh exact-plan confirmation, write, fully read back, invalidate failures, safely eject and persist evidence.
- **Tests:** Existing destructive tests plus adapter qualification, multiple candidates, internal/system/mounted/read-only/ambiguous target, identity change, stale confirmation, cancel, readback mismatch, safe eject. Linux stays disabled until its own qualification.
- **Docs:** Physical writer qualification and platform status; do not overstate disposable tests.
- **Commands:** `python -m pytest -q tests/unit/test_removable_adapters.py tests/unit/test_removable_recovery_guards.py` plus planned writer qualification tests; then full `python -m pytest -q` and `python -m mypy macloader tests --follow-imports=skip`. Physical qualification only on approved host with sacrificial media after software gate.
- **Definition of done:** Unqualified host has no write action; qualified run requires fresh exact target confirmation and complete readback.
- **Rollback boundary:** Disable campaign write action; low-level writer remains only in engineering/qualification mode.
- **Depends on:** P0, P3, P7, P8.

### P10 — Guided first boot and checkpoint results

- **Objective:** Stop at Recovery utilities and record what happened.
- **Files:** Autoloader checkpoint/session model; Guided CLI/TUI; campaign instruction keys; `docs/T480S_FIRST_INSTALL_RUNBOOK.md`; `docs/HARDWARE_ACCEPTANCE.md`.
- **Work:** Generate instructions from reviewed route policy plus USB evidence; include port action, Lenovo F12 one-time menu, picker, Recovery utilities, no erase/install, shutdown/reboot and existing OS check. Record each outcome pass/fail/not tested separately. Picker/Recovery do not promote support.
- **Tests:** Correct instructions; each checkpoint; failure/restart; unknown route blocks; no internal install action reachable; smoke labeled; success does not change support state.
- **Docs:** Runbook and acceptance checklist linked to generated instructions.
- **Commands:** `python -m pytest -q tests/unit/test_autoloader.py tests/unit/test_cli.py tests/unit/test_tui.py`; then full `python -m pytest -q`, `python -m mypy macloader tests --follow-imports=skip`, package smoke and hosted green CI.
- **Definition of done:** Normal user reaches safe endpoint and MacLoader records actual result; v1 has no install stage.
- **Rollback boundary:** Remove guided display while retaining session records and runbook.
- **Depends on:** P3, P4, P6, P7, P8, P9.

### P11 — Sanitized reference fixture and physical-campaign gate

- **Objective:** Create deterministic fixture from a real sanitized snapshot when available, then evaluate a separately authorized physical campaign.
- **Files:** New `tests/fixtures/t480s/t480s_20l8_n22et85w_162_reference.json`; state-machine tests; `docs/HARDWARE_ACCEPTANCE.md`; runbook.
- **Work:** First use an explicitly labeled synthetic candidate-shaped fixture for CI. Once actual laptop snapshot is captured, remove serial, UUID, MAC, storage serial and all unique identifiers while preserving required topology; review diff; keep raw capture out of Git. Fixture is regression input, never physical acceptance evidence.
- **Tests:** Sanitization assertions and state machine; generic 20L7 and touchscreen 1.53 fixtures cannot masquerade as exact candidate.
- **Docs:** Fixture provenance and non-evidence status.
- **Commands:** `python -m pytest -q tests/unit/test_sanitization.py tests/unit/test_autoloader.py` plus planned fixture validator; then full `python -m pytest -q` and hosted CI on the exact commit.
- **Definition of done:** Synthetic fixture covers CI before hardware; real sanitized fixture only from a real capture. P0–P10 and readiness gate are green before physical attempt.
- **Rollback boundary:** Remove fixture without altering campaign/session code.
- **Depends on:** P0–P10. Real capture requires host access and a separate physical campaign; not part of this planning pass.

### P12 — Upstream maintenance automation

- **Objective:** Turn routine dependency monitoring into reviewed candidate changes.
- **Files:** Future scheduled workflow, updater module, versioned dependency/toolchain catalogs, PR/report templates.
- **Work:** Watch authoritative upstream metadata for OpenCore, Lilu, WhateverGreen, AppleALC, VirtualSMC, IntelMausi, NVMeFix, VoodooPS2, VoodooI2C, itlwm/OpenIntelWireless, Bluetooth and tool dependencies. Fetch candidate metadata/artifacts, validate provenance/hashes, propose catalog diffs, run dependency tests, deterministic reference EFI, matching ocvalidate and full CI, then create report/PR. Never change production policy or auto-merge.
- **Tests:** No-release no-op, valid candidate, malformed metadata, hash mismatch, missing license, incompatible graph, EFI/validator/CI failure prevents proposal readiness; no auto-merge path.
- **Docs:** Maintenance cadence, source authority, human approval boundary.
- **Commands:** `python -m pytest -q tests/unit/test_upstream_updates.py`; run the candidate catalog/EFI workflow in report-only mode; then full `python -m pytest -q`, `python -m mypy macloader tests --follow-imports=skip` and the hosted CI matrix.
- **Definition of done:** Automation proposes only after complete gates and cannot update production campaign itself.
- **Rollback boundary:** Disable scheduled job; pinned catalogs/current campaign stay unchanged.
- **Depends on:** P0, P1, P7 and stable fixture/build pipeline from P11.

## L. Physical campaign readiness gate

The first real T480s USB boot attempt is prohibited until every applicable item below is true. Software/disposable-image success alone is insufficient.

### Software and campaign

- P0–P10 complete on a known green commit; hosted Ubuntu/Windows functional tests and mypy pass, package smoke passes, canonical coverage gate passes.
- Exact campaign resolves to T480s 20L8 / N22ET85W 1.62 / Sequoia 15.0 build 24A335 / P4 / configuration and evidence policies, with no dangling or contradictory records.
- Live sanitized detection on target matches machine type and BIOS; required CPU/iGPU/audio/network/storage/panel facts are observed at documented confidence. FHD/non-touch is accepted only with sufficient evidence. Conflict or unsupported component blocks.
- No generic or synthetic fixture is represented as physical evidence.

### Evidence, identity and EFI

- ACPI is captured from this machine/BIOS, privately stored, bound to current snapshot, contains exactly the validated expected set, and passes header/checksum/compile checks.
- Required USB evidence comes from actual insertion/removal actions. Build USB map and first-install route derive from accepted observations. Unresolved ports stay unresolved and are not guessed.
- Private SMBIOS identity was deliberately generated or reused and validated; serial/MLB/UUID/ROM never appear in logs, screenshots/status, Git or public exports.
- Dependencies and host tools match pinned hashes. EFI is built in campaign workspace, structurally validated, passes matching OpenCore 1.0.7 ocvalidate, and has a redacted provenance manifest bound to current inputs.
- A successful validator result is described only as a valid configuration, never as boot or hardware evidence.

### Recovery and media

- Either exact Recovery is authenticated as Sequoia 15.0 build 24A335 and has a strict lock, or owner explicitly chooses separately labeled smoke-only mode after its implementation passes source/signature/asset tests. Smoke artifact keeps build unknown and cannot authorize installation.
- Media writer backend has completed required physical qualification. Preserve ADR-007: Windows qualification precedes Linux; each OS stays disabled until separately qualified.
- Media candidate is shown with model, capacity and stable properties; no internal/system disk is eligible. Exact target is re-enumerated immediately before write and freshly confirmed. Full readback passes; failed output has durable invalidation evidence; safe eject completes.

### First boot and stopping point

- Generated instruction identifies evidence-backed first-install USB route and Lenovo F12 one-time boot flow. Current runbook notes the historical left USB-A-by-HDMI / SS01 route but requires confirming it on the actual laptop; campaign policy uses collected evidence rather than assume the old label.
- First boot is supervised and ends at the OpenCore picker and Recovery utilities screen. No Erase, Partition or Install action starts.
- MacLoader records picker reached, Recovery reached, disk untouched, shutdown/reboot and existing OS bootability as separate pass/fail/not-tested checkpoints.
- Smoke success does not promote profile to SUPPORTED or authorize internal-disk installation. Later installation requires separate phase, exact internal-target identity via multiple stable properties, and fresh destructive approval.

**Current gate status:** not ready. Current checkout has no private live ACPI capture, selected real SMBIOS identity, exact verified Recovery payload, physically qualified writer or boot-attempt evidence. CI is not green. No physical USB boot testing was performed in this planning pass.

## Specification adjustments based on repository evidence

1. **Layout 86 is already corrected locally.** Keep the correction and add a default-path regression; do not add another audio policy mechanism.
2. **P0 reflects current exact failures.** The 2026-10-02 hosted run confirms Python 3.14 coverage and Windows platform assumptions; repair them without lowering the threshold or skipping Windows.
3. **USB evidence needs build integration.** The repository has an evidence-session contract, but it currently does not create a USB map used by EFI. Wizard completion is not a readiness gate until accepted observations affect generated config and manifest.
4. **Physical writer qualification remains a hard dependency.** Guarded code and disposable tests exist, but no production writer is qualified. Preserve the Windows-first decision in ADR-007.
5. **Exact Recovery remains externally blocked.** A prototype smoke route is a reasonable separate design, but Apple signature/integrity does not prove build 24A335. Keep strict path blocked unless an exact authenticated relation appears.
6. **OpenCore 1.0.8 is an isolated tooling migration.** Its Windows macrecovery fix is useful for smoke acquisition but should not change frozen OpenCore 1.0.7 EFI baseline.
7. **No real reference fixture can be created from this workspace yet.** Use a visibly synthetic 20L8/1.62 fixture for state-machine tests; create sanitized real fixture only after an actual T480s capture.
8. **First-boot route is not yet verified.** Runbook describes left USB-A beside HDMI as historical SS01 and asks for actual-device confirmation. Guided policy renders only a route established by P6 evidence.

## Final implementation order

P0 CI/baseline → P1 campaign policy → P2 observations → P3 session/NextAction engine → P4 guided acceptance/identity → P5 ACPI capture → P6 USB evidence and map contract → P7 automatic tools/dependencies/build/map integration → P8 Recovery modes → P9 qualified media → P10 first-boot checkpoints → P11 fixture/readiness gate → P12 maintenance automation.

The product definition of done is met only when one guided launch on the exact reference machine automatically detects and matches the campaign, creates/resumes the session, reconciles observations, gathers private ACPI evidence, guides USB mapping, provisions trusted tools/dependencies, presents only identity and experimental-policy decisions, builds and validates EFI, follows a truthful Recovery mode, prepares media through a qualified writer, gives route-specific F12 instructions, records the picker/Recovery smoke result, and stops with the internal disk untouched.
