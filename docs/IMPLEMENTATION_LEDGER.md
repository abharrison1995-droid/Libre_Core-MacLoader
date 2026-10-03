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

## Autoloader P4 — Guided checkpoints

Default `macloader` and `macloader autoload` now open the native Textual guided surface; `autoload --terminal` uses the same semantic choices and `--status` exposes sanitized progress. Engineering remains available through its button and existing commands. One profile acceptance writes existing digest-bound acknowledgements; private identity generation/reuse delegates to IdentityService and never displays filenames or private values. Synthetic snapshots refuse real generation. Engineering policy drift blocks guided use without overwriting saved changes.

The guided surface uses the inherited Textual theme: header/footer, scrollable campaign summary, numbered stage, current action, contextual full-width choices, Retry and Engineering. Native focus/disabled states remain; q quits, r retries and c pauses. A skill-required read-only visual review inspected 80×24 and 100×35 captures and accepted the P4 shell after the paused capture message was clarified. This is software/UI evidence only; later phases supply capture/build/media handlers.

## Autoloader P5 — Direct firmware capture

Linux capture reads only the reviewed kernel table set twice, bounds byte reads, checks table-specific signatures/lengths/checksums and rejects changed inputs. When OS permissions require it, a fixed read-only `pkexec /usr/bin/python3 -I` helper reads the same kernel scope into a private pipe; the elevated process cannot import project modules or choose output paths. A missing OS helper or denied prompt is an explicit permission blocker. The application itself stays unprivileged. Windows capture uses catalog-pinned ACPICA 20260408 acpidump, checks its hash/banner and owns its private staging path. Nonzero capture exit remains blocked, including the historical tool's reported -1 exit, rather than claiming success from partial output.

WorkflowService.collect_acpi verifies snapshot identity and affected hardware scope after capture, imports through the existing strict boundary and attaches scoped evidence. The guided handler independently rechecks private machine binding. Synthetic fixtures cannot capture real host firmware. Windows executable records were downloaded from the official ACPICA/OpenCore releases, independently hashed and added without changing OpenCore 1.0.7. ACPICA copyright/license/disclaimer is included in packaged data.

Correction: TABLE_NAMES formerly required DSDT + twelve SSDTs despite its eleven-SSDT policy description. The real historical report records eleven SSDTs; the thirteen-table research result was software build evidence, not current physical evidence. The filename set now requires DSDT + eleven SSDTs, with an independent count regression and strict signature checks. Earlier private thirteen-table imports need recollection; no legacy artifact is grandfathered as valid.

P4 commit 8e32a7f passed both hosted CI runs (37129969242, 37129971264). P5 local gates: 532 tests passed; branch coverage 79.05%; native and Windows-stub mypy clean across 123 files. The narrow helper and Windows process boundary are mocked in CI; no host elevation, real capture or physical claim occurred.

### Campaign Ethernet correction

Historical sanitized reference documentation records Intel I219-V, while the first campaign composition linked only I219-LM. Existing component policy reviews both variants through the same IntelMausi path. Campaign revision 2 now links both existing component records; actual detection chooses compatibility, and unknown devices still block. A regression checks I219-V. This broadens the explicit reviewed Intel variant set without importing historical observations as current physical evidence. P5 commit 14b4bbf passed both hosted runs 37130481261 and 37130483618.

## Autoloader P6 — Physical USB collector

Added provider events, strict literal firmware address extraction and a resumable movement wizard using existing USB observations/session. Existing observation schema gains optional firmware address/namespace; older imports remain readable but cannot supply a generated map without address proof. Completeness supports speed companions and two orientations on the same physical connector while rejecting duplicates and conflicting reuse of a logical route. Campaign-linked evidence scope requires two USB-A speed tests, two USB-C orientation/speed sequences and proven internal camera/Bluetooth/card-reader routes; WWAN is excluded. USB-C uncertainty is preserved rather than inventing a route. The guided screen watches events without asking for JSON, controller, speed or port names. No actual port moves were performed: all event sequences in tests are synthetic.

Windows provider is an explicit external capability gap: the existing read-only snapshots retain stale device nodes and do not prove socket-to-ACPI correspondence. Linux and Engineering import can proceed; Windows capture cannot claim success. Generated-map consumption is P7, not established by wizard completion alone.

## Autoloader P7 — Safe software preparation and consumed USB map

Registered safe tool/dependency/build handlers. Identity and acknowledgement precede dependency resolution to honor existing accepted-plan prerequisites. Builds own protected versioned output, validate structural and matching ocvalidate results, preserve private diagnostics, and validate before crash-recovery adoption. Configuration changes invalidate derived artifacts/checkpoints immediately.

Added mandatory profile-bound USB input to WorkflowService/Orchestrator/EfiBuilder. A codeless injector now becomes a real EFI file and Kernel entry; raw evidence and generated plist digests bind the manifest and strict Recovery derivation. Current hardware topology and private ACPI literal addresses corroborate the map. Changed raw firmware is rejected before configuration acceptance. No arbitrary engineering plist overlay is introduced.

Local integration uses synthetic archive contents and declared events, never physical evidence. A separate run with MACLOADER_VERIFY_PINNED_TOOLS=1 passed the profile/map config through the verified real OpenCore 1.0.7 ocvalidate. Synthetic artifact contents are nonbootable; this establishes config/software validation only. P6 host-boundary repair 3bb0882 passed hosted runs 37131922378 and 37131926287 after replacing process-global platform injection with a local provider seam; only the POSIX kernel-filename fixture is Windows-gated.

P7 gates: 558 tests passed, 79.66% branch coverage; mypy native and Windows stubs clean across 128 files. Wheel and sdist built and each passed the outside-checkout package smoke command. The explicit real pinned-tool config/map integration also passed. Hosted results are checked after publication.

## Autoloader P8 — Qualification and smoke Recovery separation

Exact discovery is attempted automatically, then exposes explicit Retry exact / Smoke test only choices. Qualification does not mint a lock from AP or signed bytes. The separate smoke policy references reviewed 1.0.8 protocol source, explicitly treats legacy metadata as untrusted, rejects redirects/unsafe asset schemes, uses bounded HTTPS acquisition and existing Apple chunklist/image verification. A distinct immutable smoke record cannot claim an actual build, qualify the target or authorize installation. Requested target stays 15.0/24A335. Restart re-verifies signed bytes before adopting a persisted smoke record. See ADR-008 for the explicit protocol migration and replay limitation. P7 commit 7b6e7ee passed both hosted CI runs 37132625887 and 37132628597.

### P9 — Guided media contract (2026-10-03)

Added `autoloader/media.py`: private, resumable source preparation; campaign-bound
smoke verification; real OpenCore `com.apple.recovery.boot/BaseSystem` layout;
unsafe target filtering; stable model/size/opaque identity selection; one explicit
erase decision using the existing expiring confirmation; immediate target/source
rechecks; full readback, failure invalidation and required safe eject. Selection
and consent are memory-only and cannot survive restart. The journal conservatively
records possible destructive I/O before crossing the writer boundary, including
failed attempts and later configuration changes. Guided clients never promise
that no write occurred after that boundary.

Extended existing `MediaBindings` with an exclusive smoke purpose and separate
smoke digest; qualification serialization and exact-lock verification remain
unchanged. The writer re-verifies the Apple signature, campaign target, source
bytes, boot-layout copies and policy at publication. Smoke records cannot satisfy
qualification bindings. Disposable copying now retains root publication files.

**Physical enablement remains blocked:** no qualified native Windows backend is
supplied by this checkout, and ADR-007 Windows-first physical qualification has
not occurred. Safe-eject availability is separately required. Linux remains
unqualified. This phase supplies tested integration, not writer qualification;
no physical device has been written. Disposable tests cover hot swaps, unsafe
internal targets, corrupt source/readback, signature checks and eject.

P8 hosted push/PR CI was green at e76a24c (runs 37133597691/37133600655).

P9 local gate: 564 tests passed, 79.15% branch coverage (79% required);
native and Windows-target mypy passed (132 files); wheel and sdist smoke passed
outside checkout. Physical writer qualification remains an external campaign gate.
