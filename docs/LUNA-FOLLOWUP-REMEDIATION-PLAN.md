# MacLoader Luna follow-up remediation plan

Status: implemented follow-up plan. The three standalone Luna/high reviews
were used as input; implementation was performed in this checkout without
delegated sub-agents. See the implementation ledger for the exact change and
verification record.

## Decision

The candidate remains physically release-blocked, but the confirmed shared
workflow and trust-contract failures in this follow-up are now repaired. The
remaining release boundary is the documented external qualification work.

This plan supplements
[`workspace/readiness-improvement-plan.md`](../workspace/readiness-improvement-plan.md);
it does not replace that specification or silently downgrade any of its gates.

## Non-negotiable constraints

- Preserve unrelated working-tree changes. Do not reset, commit, push, or
  publish without separate authorization.
- Do not enable physical USB writes, generate real private identities, change
  firmware/BIOS, alter internal disks, or install macOS.
- Keep structural checks, fake tools, fake identities, fixtures, and synthetic
  adapters explicitly test-only. They must never produce production
  qualification.
- Fix shared service boundaries first so CLI and TUI cannot diverge.
- A digest-shaped value is not provenance. Every qualified result must be
  derived from, and rechecked against, the actual current artifacts.
- Record each change, regression test, and remaining qualification gate in
  [`workspace/implementation-ledger.md`](../workspace/implementation-ledger.md).

## Consolidated blockers and work packages

### WP0 — Reproduce and freeze the current baseline

Before changing code:

1. Run the focused readiness, Recovery, EFI, CLI, TUI, cache, identity, and
   removable-media suites separately with per-test timeouts.
2. Record the exact failures currently reported by the swarm:
   - Recovery binding test failure at
     `tests/unit/test_readiness_repairs.py:331`.
   - TUI worker-stage test exceeding its 20-second timeout.
   - Any focused suite that stalls around 60%.
3. Confirm the working-tree diff and update the ledger with a `Luna follow-up`
   entry without claiming the old 411-test result as current.

Exit criteria: deterministic reproductions exist, the hanging test is isolated
or explicitly marked as an unresolved blocker, and no baseline test is changed
just to make the report green.

### WP1 — Repair the effective-profile and Recovery capability contracts (P1)

#### 1.1 Canonicalize the profile digest contract

Define one named value for the effective profile/configuration identity. It must
be clear whether it represents the reviewed raw profile, the effective profile
after accepted selections, or both as separate fields. Use the same contract
in:

- `macloader/configuration/service.py`;
- `macloader/workflow/service.py`;
- `macloader/build/efi.py`;
- the EFI manifest and Recovery binding;
- persistence/reload validation.

Do not compare an effective configuration digest with a raw profile digest.
Add an end-to-end test proving that the shared workflow reaches the builder and
that audio layouts 11 and 86 produce the corresponding `DeviceProperties` and
`alcid` values.

#### 1.2 Make verified Recovery bindings workflow-issued by construction

Replace any public path that can turn caller-supplied digest fields into a
strict binding with an issuing operation owned by the live shared
`RecoveryService`. The workflow must pass the current verified configuration,
plan, toolchain, EFI manifest, target policy, and artifact evidence to that
operation; callers must not supply the relationship fields independently.

Keep structural/unverified inspection as a separately named mode with a
separate result type/status. Persist a verified Recovery generation that can be
revalidated after reload; do not persist or log a live capability secret.

Required tests:

- workflow-issued binding succeeds through acquire, persist, reload, and
  verify;
- direct forged, stale, foreign, mutated, and dataclass-replaced bindings fail
  by default;
- configuration, plan, toolchain, EFI, target, or Recovery artifact changes
  invalidate the prior generation;
- the existing stale-identity test is rewritten to use the shared workflow
  path, while retaining a direct-bypass rejection test.

#### 1.3 Repair or remove the legacy CLI build route

Route the ordinary `build` command through the same workflow service used by
the TUI. It must resolve the reviewed profile, machine-bound ACPI evidence,
trusted toolchain, deliberate identity reuse, effective configuration, and
matching validation before invoking EFI generation. If those prerequisites are
unavailable, expose an actionable blocked result and the next corrective step.

If the legacy route cannot be made safe in this series, remove or explicitly
deprecate that user-facing route rather than leaving a cosmetic command that
always fails or bypasses safeguards.

Required tests: fresh installed-workspace CLI preparation, qualified versus
structural mode exit/status behavior, missing-prerequisite remediation, and
CLI/TUI equivalence through the shared service.

### WP2 — Repair worker teardown and operation lifecycle (P1/P2)

#### 2.1 Isolate and fix the TUI hang

Use real Textual pilot tests and a bounded timeout to identify which worker or
awaited callback remains alive after dependency/build/verification actions.
Make unmount, cancellation, retry, and supersession follow one operation
state machine:

- each operation owns a cancellation token and generation;
- unmount invalidates the generation, requests cancellation, and waits only
  within a documented bound;
- results are immutable snapshots and are rechecked against the current
  generation immediately before every state mutation or persistence write;
- save/import/refresh actions invalidate affected generations;
- cancellation and stopped states are distinct from success and failure.

Required tests cover 80x24 and 100x35 real interactions, cancellation during
each long stage, retry after cancellation, unmount during each worker, and
superseded completion after a newer save/import.

#### 2.2 Make interruption cleanup unconditional

Ensure downloader, ACPI, EFI, and validator subprocesses clean up on
`BaseException`, including `KeyboardInterrupt`, without leaving child
processes or partial publication. Use process groups or an equivalent
controlled termination/wait strategy where external tools are involved.

Required tests inject interruption at pre-start, during execution, and just
before publication; assert child cleanup, temporary-file ownership, and no
partial authoritative state.

### WP3 — Close persistence, cancellation, and resource-boundary gaps (P2)

#### 3.1 Enforce CAS at the lowest configuration API

Prevent `ConfigurationStore.save()` from silently replacing a newer document
when `expected_revision` is omitted. Choose and document one safe API:
require the loaded base revision for replacements, or provide a distinct
create-only operation for new documents. Keep the read/check/publication lock
held across the transaction and preserve valid backups after interruption.

Add a direct-store concurrent test where a stale draft has a numerically higher
local revision but still loses against the loaded base revision.

#### 3.2 Propagate cancellation through dependency resolution

Pass the operation token/callback into graph traversal and resolution, not only
around the resolver. Check it at bounded work units and before publication.
Test cancellation during traversal, download, extraction, EFI generation, and
validation.

#### 3.3 Bound Recovery state reloads

Apply explicit size and structural limits before reading/parsing local Recovery
state, lock, and evidence JSON. Reject oversized, malformed, or excessive
nested data before allocation. Add tests for oversized files, malformed JSON,
and valid near-limit state.

### WP4 — Harden remaining trust and media boundaries (P2)

#### 4.1 Separate synthetic qualification from production qualification

Make synthetic tool/validator injection private to test fixtures or require an
unrepresentable test-only type. Production code must reverify catalog identity,
bytes, version, and validator provenance immediately before qualified output.
Add tests showing that forged `qualified` labels and exit-zero stubs cannot
produce a production-qualified result.

#### 4.2 Reconcile all media bindings with actual artifacts

At the final media planning boundary, recompute and compare configuration,
profile, plan, toolchain, EFI manifest/payload, Recovery lock/evidence, image,
and chunklist hashes/sizes from the actual files. Reject caller-constructed
shape-valid plans before any adapter callback. Retain physical writes disabled
until destructive-adapter qualification and readback gates pass.

#### 4.3 Harden ACPI and identity storage

- Reject symlinked ACPI capture roots and table files, including symlinked
  ancestors, before reading or publishing evidence.
- Verify Windows ACLs before identity reuse; retain atomic private publication
  and no-follow behavior on POSIX and Windows paths.
- Add adversarial tests for symlink swaps, permission changes, interrupted
  writes, ACL broadening, and reuse of modified identities.

## Verification sequence

Run each stage before moving to the next:

1. WP0 focused reproduction and ledger update.
2. WP1 unit, workflow, CLI, EFI, Recovery, and installed-workspace tests.
3. WP2 real TUI interaction, cancellation, interruption, and worker lifecycle
   tests with bounded timeouts.
4. WP3 deterministic concurrency/race tests using barriers, not sleeps, plus
   bounded-resource tests.
5. WP4 adversarial trust/media/identity tests.
6. Full repository tests, branch coverage gate, CI-equivalent mypy, compile and
   diff checks, wheel/sdist builds, and fresh isolated package smoke tests.

The release gate is not met if any P1 remains, any focused test hangs, a
qualified path accepts synthetic or caller-asserted provenance, or the
installed package cannot complete the guarded software workflow.

## Separate external qualification gates

These are not replaced by local tests or fixtures:

- private trusted production toolchain installation and independent
  version/digest verification;
- real Apple Recovery acquisition, signed chunklist verification, and
  long-download/resume behavior;
- native Windows ACL and removable-device lock/readback qualification;
- physical USB media testing with destructive writes still separately gated;
- real T480s boot/acceptance testing against the reviewed BIOS and evidence;
- macOS installation and post-boot validation.

Until those gates pass, report the result as software-preparation ready only,
not physically installation-ready or T480s-supported.

## Ledger updates required at closure

For every work package, record changed files, tests and exact results, status
of each affected F finding, unresolved defects, and the external qualification
requirements. Replace the current `Implemented` status for any finding that is
shown by this follow-up to have a remaining software defect; do not count a
passing synthetic test as production qualification.
