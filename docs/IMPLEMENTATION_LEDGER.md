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

### P10 — First-boot procedure and explicit reports (2026-10-03)

`autoloader/first_boot.py` generates Lenovo F12 instructions from the campaign
and the current proven physical USB route. Seven deliberate checkpoints cover
picker, Recovery selection, utilities, no erase, no installation, shutdown/reboot
and existing-OS bootability. Results are bound to configuration/campaign/media;
upstream changes clear them. Failed checkpoints remain failed until explicitly
repeated. Resume never infers a physical result. EFI and signed Recovery are
reverified before instructions. The terminal state is **reported smoke results**,
not support, exact-build qualification or installation authority.

Before a guided write, current bound laptop facts are probed again; any change
requires reconciliation. No real boot/write/identity was performed in this phase.
Tests use explicit disposable/synthetic seams and exercise all reports,
failure/retry, missing route proof and downstream invalidation.

P10 local gate: 567 tests passed, 79.19% branch coverage; native and Windows-target
mypy passed (134 files); wheel and sdist outside-checkout smoke passed. P9 hosted
push/PR CI was green at 0bff097 (37134038810/37134041856). Physical first boot was
not attempted.

### P11 — Reference-fixture privacy and read-only readiness gate (2026-10-03)

Added live-capture-to-regression sanitization (no actual reference capture was
supplied), explicitly non-evidence fixture guards, and `autoload --readiness`.
Readiness uses exact actual GitHub jobs rather than historical test counts or a
caller-supplied ready flag; dirty software, missing jobs or unknown evidence block.
The report never authorizes writes/installation. Owner documentation edits remain
outside implementation commits.

Connected the remaining unknown-panel seam to existing HardwareConfirmation with
optional scope/digest fields, backward-readable for old records. Only unknown
panel touch can receive the focused physical non-touch confirmation; positive
observations always prevail and machine/BIOS/panel drift invalidates it. Other
required unknown facts remain blocked for focused provider/evidence work.

P11 real fixture and physical results remain external/unperformed; the synthetic
fixture is not renamed or promoted. Hardware acceptance documents exact missing
gates, including Windows-first writer qualification and unavailable Windows
physical USB firmware correlation.

P11 local gate: 571 tests passed, 79.22% branch coverage; native and Windows-target
mypy passed (137 files); wheel and sdist outside-checkout smoke passed. P10 hosted
push/PR CI was green at cc4cb50 (37136315619/37136318628).

### P12 — Reviewed upstream candidate automation (2026-10-03)

Added `maintenance/upstream.py` and the scheduled/manual/report-only branch
workflow. Sources derive from existing catalogs; no separate upstream registry.
Stable authoritative release assets, hashes, bounded acquisition and matching
source license notices are required. Candidates use an isolated catalog overlay
and the existing Database/DAG. OpenCore/ACPICA/tools produce separate migration
reports. Only one independent candidate is proposed at a time.

Actual candidate archives now feed the deterministic EFI harness when explicitly
enabled; the matching pinned validator really executes. Full four-cell CI,
canonical coverage and wheel/sdist gates precede the verified report/draft PR.
No auto-merge or production catalog change exists. Existing proposal branches are
preserved. Scheduled publication requires GitHub's Actions-PR setting; the
schedule activates after merging the workflow to the default branch. Branch
verification and default manual runs are report-only.

Live report found verified WhateverGreen 1.7.1, VirtualSMC 1.3.8, AppleALC 1.9.8;
OpenCore 1.0.8 and ACPICA 20260930 require versioned migrations. The first real
WhateverGreen overlay passed verified full dependency acquisition, deterministic
EFI generation and real matching OpenCore 1.0.7 ocvalidate locally (1 test passed,
6.82s). Synthetic ACPI/identity/route seams remain explicitly nonphysical; no
boot claim. Full base test gate prior to final workflow assertions: 580 tests,
79.31% branch coverage, native/Windows mypy 140 files passed.

P11 hosted push/PR CI was green at b8710ca (37136780533/37136783767).

P12 local gate: 581 tests passed, 79.31% branch coverage; native and Windows-target
mypy passed (140 files); wheel/sdist outside-checkout smoke passed. The scoped
Impeccable review returned **ship** for the native USB selection, erase and
first-boot UI at 80×24/100×35, after recapturing complete production copy and
expanded/selected targets. Its documenter appended preservation evidence to
Architecture. Screenshots are synthetic; no physical result is recorded.

### Final integration hardening (2026-10-03)

Reject all-zero/all-ones/malformed DMI UUIDs and known placeholder serials before
private campaign binding; retain existing valid identifier formats. Unexpected
saved-state failures become safe structured blockers. Failure copy preserves
historical possible destructive I/O instead of promising nothing happened.
Unknown component messages use plain hardware names. Terminal USB polling no
longer repeats unchanged summaries. Quit during a worker requests cancellation
and waits for its guarded boundary; 3+ target selection stays disabled until an
actual choice is selected. Native focus/scroll/theme remain unchanged.

Corrected the earlier no-configuration preflight wording: an unassessed snapshot
binding is missing, not a mismatch. Exact-target logic now references the existing
Recovery policy instead of scattered version constants. CLI unit discovery now
injects a Windows adapter result rather than querying a CI runner's real disks;
real adapter behavior remains covered by dedicated provider tests. The first
candidate workflow failed closed on that Windows/Python 3.11 test and skipped
publication (37137988988). Three other candidate cells passed their complete
real-acquisition/EFI/validator/package gates. Standard P12 push/PR CI was green at
1ca4417 (37137988982/37137991401). A full corrected hosted candidate run is required.

README now makes Guided Autoloader the default and links a dedicated implementation
status page. The two pre-existing owner documentation edits remain uncommitted and
preserved; they are not bundled into implementation commits. The previously
corrected owner layout-86 YAML was integrated separately at cbc0a05.

Final local integration gate: 586 tests passed, 79.50% branch coverage; native and
Windows-target mypy passed (140 files); wheel and sdist built and their
outside-checkout smoke checks passed. The explicit real pinned-tool reference
EFI/ocvalidate test also passed (1 test). Hosted verification follows below.

The final scoped reviewer caught and rechecked a worker-entry cancellation race.
Cancellation now resets on the UI thread before scheduling, never at worker
entry; a regression quits between scheduling and entry and verifies the cancel
signal survives. Recheck disposition: **ship**.

Corrected candidate run 37139038655 exposed a Windows/Python 3.11 late
Select message after screen unmount. The handler now ignores a missing native
button during teardown; a direct unmounted-message regression covers this
lifecycle boundary. Publication again remained blocked until all cells pass.
The teardown regression covers both no screen stack and an existing screen with
its button removed. Scoped recheck found no visual or choice regression. Final
hosted run links will be retained in draft PR #1.

### Native Windows writer qualification boundary (2026-10-03)

Verified starting branch/HEAD 4dd066d and draft PR #1; hosted standard and candidate
gates remained green. Owner Project Status/runbook edits are preserved separately.
Added windows_native.py/windows_image.py/windows_qualification.py, an empty
packaged versioned approval catalog, qualification documentation and scoped tests.
current_adapter connects the native implementation on Windows without granting
qualification. Production qualification derives only from an exact reviewed
backend-source/Windows-build/AMD64 record; local reports do not enable it.

Native whole-disk descriptor/system-volume checks, volume GUID lock/dismount,
sector-aligned raw writes, flush, complete uncached readback, verified signature
invalidation and non-persistent confirmed native disk offline are implemented.
Guided qualification gate is unchanged. Added cancellation propagation to native
bounded chunks; fixed numeric USB BusType parsing exposed by the native boundary.

The separate physical harness uses explicitly non-bootable qualification data,
existing source snapshot/confirmation guards, actual exact-commit CI checks and
private before-I/O reports. Normal and controlled injected-fault cases remain
labelled; unperformed cases stay not-run. Physical case reporting never changes
production approval. Approved-source edits or Windows build drift revoke matching
approval. GPT/FAT output was independently checked using disposable regular files
with sgdisk, read-only fsck.fat and mtools Recovery file readback. This exposed and
fixed root volume-label/free-cluster-summary metadata before final verification.
No physical storage I/O, real identity generation, ACPI capture or boot occurred.

Native writer local software gate: **658 tests passed, 80.31% branch coverage**;
native and Windows-target mypy passed (144 files); wheel/sdist build and both
outside-checkout package smoke checks passed. The explicit real pinned reference
EFI/ocvalidate test passed (1 test). The 72 new native/image/qualification cases
use synthetic boundaries or disposable regular files; zero physical results.
Hosted exact-commit standard and candidate gates are required before the harness
can proceed; final run links are retained in draft PR #1.

Approval hardening: source fingerprints normalize Git CRLF to LF for consistent
Windows-checkout/wheel binding while rejecting other source changes. Injected
API objects (including a Win32Storage test DLL) cannot inherit production approval.
Final local gate: **660 tests passed, 80.31% branch coverage**, both mypy targets
passed (144 files), wheel/sdist and outside-checkout smoke passed, and the explicit
real reference EFI/ocvalidate test passed. The initial native commit aded8fb had
green hosted PR matrix 37147168471 and candidate gates 37147166193; final hardening
requires its own exact hosted verification before hardware use.

Read-only current host check: Linux / MSI MS-7C02 / BIOS 3.K0, not the T480s.
There is no native Windows or laptop physical access in this session. No physical
case, real capture, identity decision, EFI campaign or boot result is recorded.
