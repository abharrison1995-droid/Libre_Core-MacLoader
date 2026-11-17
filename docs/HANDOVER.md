# Handover to the next implementing agent

Written 2026-11-17 at `main` = `origin/main` = `dddd37e`. Read this page, then [AGENTS.md](../AGENTS.md), then section 0 and the "Progress" table of the [T480s first-install fix plan](T480S_FIRST_INSTALL_FIX_PLAN.md). The plan is the source of truth for what each chunk changes; this page tells you how to pick up the work and how to hand it back.

## 1. What the project is and where it stands

MacLoader is a Python 3.12 Click/Textual tool that prepares an OpenCore USB installer for one experimental target: ThinkPad T480s 20L8, BIOS N22ET85W 1.62, macOS Sequoia 15.0. The development host is Windows 11 on that T480s. The goal is the owner's first macOS installation.

Done so far, all pushed:

| Part | Result |
|---|---|
| P0.1, P0.6 | Baseline recorded, then made green |
| Phase 1 (P1.1-P1.7) | Windows hardware detection fixes |
| Phase 2 (P2.1-P2.6, P2.2b) | Session engine fixes |
| Docs | Fix plan committed as the single active plan; stale plans removed |

**Baseline at `dddd37e`:** 753 passed, 16 skipped, 1 xfailed, 0 failed; `mypy macloader` "Success: no issues found in 88 source files". The xfail (`tests/unit/test_autoloader.py::test_resume_keeps_artifacts_with_extra_usb_keyboard`, strict) is deliberate; see P2.2b. Two tests are intermittent (see section 4); P2.7 fixes them.

Decisions taken while implementing are written next to each chunk in the plan ("Orchestrator decision", "Orchestrator correction", "Result"). Read them before touching related code. In short:

- P1.2: WWAN rows are dropped by the Windows detector; no snapshot field was added.
- P1.5: generic mouse rows are skipped; Synaptics/ELAN names count as a trackpad only on pointing-device rows.
- P1.7: an unreadable manufacturer is `""`, not `None`.
- P2.1: the write gate compares `machine_facts` (fixed identity), not `hardware_facts`.
- P2.2b: `stable_snapshot_digest` leaves `usb_devices` out; an external USB keyboard still invalidates earlier work (by decision).
- P2.6: `MACHINE_IDENTITY_UNAVAILABLE` and `SESSION_FILE_UNREADABLE` are new codes; a warning is prefixed to the next action's message.

Deferred on purpose, **do not fix them inside other chunks**: Phase 12 items I21 (typed WWAN field), I22 (PM981 from Windows, UASP disks), I23 (Linux removable disks and `usb_count`), I24 (a suite test runs a live probe).

## 2. What to do next

Work in this order and stop at the end of it for review (section 5). Every item here can be done without the owner at the machine.

1. **P2.7**: make the two intermittent UI tests deterministic. Do this first; until it lands, a red suite is ambiguous.
2. **P3.1**: Windows ACPI capture accepts a valid set regardless of exit code.
3. **P3.3**: resolve generated SSDT names against the captured DSDT. It reuses and moves the nesting-aware tokenizer in `macloader/evidence/usb_capture.py`; keep that module's tests passing.
4. **P3.4a, P3.4b, P3.4c, P3.4d**: one chunk each (PLUG, USBX, PNLF, EC).
5. **P3.5**: bind captured tables to the live BIOS; delete decompiled intermediates.
6. **P4.1**: add `OpenHfsPlus.efi` (not `HfsPlus.efi`, not `ApfsDriverLoader.efi`).
7. **P4.3, P4.4, P4.5, P4.6**: kext set from the profile, kext order, structural validation, iGPU check and stale profile data. P4.3 and P4.6 edit `macloader/database/data/profiles/t480s/p4-sequoia.yaml`: bump only the campaign `revision`, by one per chunk (profiles have no revision field; see the review decision under P4.3).

**Do not start these; they need the owner's facts first:**

- P3.2 needs the SSDT count from P0.3.
- P4.2 needs the `Sample.plist` table from P0.2.
- P5.1, and so P5.2 and P5.4, need the owner's USB location-path dumps.
- P0.2-P0.5 are owner steps. P0.5a (checking the BIOS table against a public reference) is reserved for the reviewing agent.

If you finish the list above, stop. Do not continue into Phase 5, 6 or 7 without a review.

## 3. How to run each chunk

You are both the worker and the orchestrator described in the plan, so apply both halves:

1. Read the chunk in the plan, including any decision notes beside it. Use search to find the code: plan line numbers are from `d00714e` and have shifted.
2. `git switch -c plan/<chunk-id>` from `main`.
3. Confirm every `[R]` claim in the code before changing anything. If a claim is false, or the code does not match the chunk, or the fix needs a file outside the chunk's list, **stop that chunk**: write what you found as a "Result" bullet under the chunk in the plan, and move to the next independent chunk instead of improvising.
4. Make the change and add the chunk's tests. Tests must use invented values only.
5. Verify: the chunk's test files, then the whole suite and mypy (commands in [AGENTS.md](../AGENTS.md)). Check pytest's exit status itself; do not trust a piped `tail`. Then run `git diff --check`.
6. Commit on the branch with one commit per chunk, a message starting with the chunk id (for example `P3.1: ...`), then fast-forward local `main` (`git switch main`, `git merge --ff-only plan/<chunk-id>`).
7. In the plan, add a short "Result" bullet under the chunk (commit, what was confirmed, any deviation) and update the "Progress" table.
8. **Never push.** The owner pushes after review.

## 4. Environment notes

- `python` is not on PATH. Use `.\.venv\Scripts\python.exe -m ...` from the repo root. The full suite takes about 2-2.5 minutes.
- PowerShell 5.1 mangles multi-line `git commit -m` strings containing quotes: write the message to a file and use `git commit -F <file>`.
- Git prints LF-to-CRLF warnings on new files; they are harmless.
- The GitHub CLI (`gh`) is not installed, so hosted CI cannot be read from here.
- **Intermittent tests:** `tests/unit/test_guided.py::test_retry_reprobes_only_for_hardware_and_binding_blockers` (about 1 in 6 runs of that file) and `tests/unit/test_readiness_repairs.py::test_tui_apply_target_is_clickable_at_supported_small_sizes` (rare). Until P2.7 lands, if one of these is the only failure, rerun once and record it; two failures in a row is a stop.
- `tests/unit/test_guided.py::test_late_selection_message_after_unmount_is_harmless` triggers a real read-only hardware probe of this machine during the suite (I24). Leave it alone.

## 5. Hand-back for review

When you stop, the owner asks the reviewing agent to check your work, commit by commit: every commit on `main` after the one that added this page (find it with `git log --oneline -- docs/HANDOVER.md`). Leave the following.

- **Commits:** one per chunk on local `main`, each starting with its chunk id; nothing pushed.
- **Branches:** keep each `plan/<chunk-id>` branch so it can be compared with `main`.
- **Plan:** a "Result" bullet under every chunk you touched, and the "Progress" table updated, including chunks you stopped on and why.
- **A summary appended to section 6 of this page:**
  - the final suite and mypy results;
  - every deviation from the plan text and why;
  - every chunk stopped or skipped and what blocked it;
  - anything you believe is wrong in the plan itself.

## 6. Hand-back log

_The next agent appends its summary here._

### 2026-11-17 implementation hand-back

Local `main` has one commit per chunk in section 2, with a matching `plan/<chunk-id>` branch and Result entry. The work started at handover commit `4726fc5`. The commits are ready for owner review before push.

| Chunk | Commit | Outcome |
|---|---|---|
| P2.7 | `bcd72df` | Waited for Textual button actions and the Retry worker. Before the change Retry failed once in 20 runs and Apply target passed 20 of 20. Both passed 30 consecutive runs after the change. |
| P3.1 | `c77b993` | Filtered Windows capture to DSDT and SSDT, then validated the set regardless of the tool's exit codes. |
| P3.3 | `3edb7e0` | Stopped. Recording the PNLF omission in the EFI manifest needs files outside the chunk. |
| P3.4a | `337a4ea` | Skipped. PLUG needs the CPU path from P3.3. |
| P3.4b | `5ed6491` | Added USBX power properties, the zero-function `_DSM` response and Darwin-only status under the system bus. |
| P3.4c | `9d5bd77` | Updated the PNLF body with the reviewed Kaby Lake UID and Darwin-only status. |
| P3.4d | `5daf7c8` | Skipped. EC emission depends on the EC path from P3.3. Recording native EC also requires a manifest change. |
| P3.5 | `86724d3` | Stopped. Import already checks the normalized snapshot BIOS through campaign matching. Intermediate cleanup still needs work. |
| P4.1 | `e81e725` | Packaged OpenHfsPlus, ordered the driver entries and checked required driver files and config entries. |
| P4.3 | `68a1b51` | Stopped. The handover requires a profile revision bump, but the profile and loader have no revision field. |
| P4.4 | `e652302` | Ordered parent bundles before plug-ins, set the Bluetooth load order and added the Lilu dependency. |
| P4.5 | `44cf1c1` | Derived executable presence from extraction and checked kext plist, executable and ACPI paths during structural validation. |
| P4.6 | `plan/P4.6` | Stopped. Confirmed the graphics and stale-data findings. The same profile revision mismatch blocks the changes. |

The final full suite had **765 passed, 16 skipped, 1 xfailed and 0 failed**. Mypy passed for 88 source files. The expected failure is the strict USB-keyboard resume case from P2.2b. P2.7 also passed two consecutive full suites at its own checkpoint. The data-loader and reference-campaign tests passed after each catalog edit. `git diff --check` passed for each commit.

P3.4b and P3.4c could proceed independently because their table bodies use the system bus and require no device path from P3.3. The PNLF UID is documented in the OpenCore sample referenced by WhateverGreen. P4.1 and P4.5 also updated the synthetic build path so it meets the new driver and kext structural checks. Direct schema-generator calls can retain their previous defaults when no extracted bundle information is supplied.

The plan needs to resolve whether the P3.3 and P3.4d omission notes belong in the ACPI qualification report or the EFI manifest. P3.5 should account for the existing BIOS gate when scoping intermediate cleanup. P4.3 and P4.6 need an explicit profile revision migration or a campaign-only revision rule. P3.2, P4.2, Phase 5 and later, and the owner steps remain deferred.

### 2026-11-17 review of the hand-back

Reviewed every commit from `bcd72df` to `3fd8998` against its chunk. The full suite was rerun on `3fd8998`: 765 passed, 16 skipped, 1 xfailed, exit 0. Mypy was clean.

- **Accepted as merged:** P2.7, P3.1, P3.4b, P3.4c, P4.1, P4.4 and P4.5. No code changes were needed. The non-blocking notes are under P4.5 in the plan; the kext executable lookup is Phase 12 item I25.
- **All four stops were correct.** Each one came from an error in the plan or in this handover. The review decisions are now in the plan:
  - P3.3 and P3.4d record omissions in a new `notes` field on the ACPI result. The EFI manifest is not touched.
  - P3.5 is cut to the cleanup of intermediate files only, because the BIOS gate already exists.
  - P4.3 and P4.6 bump only the campaign `revision`. Profiles have no revision field, and `effective_profile_digest` already binds their content.
- **Remaining work list, in order:** P3.3, P3.4a, P3.4d, P3.5, P4.3, P4.6. Section 3 applies unchanged. Hand back again under a new heading in this section.

### 2026-11-17 follow-up implementation hand-back

All six review chunks landed in order on local `main`, starting from review commit `295edcf`. The existing plan branches were fast-forwarded from their earlier stop commits before implementation and retained at the new chunk commits.

| Chunk | Commit | Outcome |
|---|---|---|
| P3.3 | `adfb7f4` | Shared the DSL tokenizer with USB evidence and resolved the ACPI namespace before generation. Native PNLF suppresses the generated table and records an omission in the qualification report and digest. |
| P3.4a | `c79d27e` | Generated the Darwin-guarded `plugin-type` method on the resolved CPU. |
| P3.4d | `c83baa3` | Generated Darwin-only EC when firmware uses another EC name. The native EC omission is recorded in the qualification report and digest. |
| P3.5 | `dbb8ce0` | Removed the captured DSL, recompiled OEM tables and raw diagnostics after success or failure. Retained generated DSL, AML and digests. |
| P4.3 | `53c85a5` | Blocked missing reviewed profile capabilities and bumped the campaign revision from `3` to `4`. |
| P4.6 | `plan/P4.6` | Bound the iGPU check to the accepted snapshot before generation, removed HS06, corrected the Sequoia Wi-Fi note and bumped the campaign revision to `5`. |

Each chunk passed its targeted tests, the full suite, mypy and `git diff --check` before fast-forward. The reference-campaign and database-loader checks passed after both data edits. The final full suite had **777 passed, 17 skipped, 1 xfailed and 0 failed**, exit 0. Mypy passed for 89 source files. The expected failure is the existing strict USB-keyboard resume case. The new directory symlink test skipped because the host could not create the link. The ACPI tests used invented DSL and a synthetic compiler.

The implementation followed the review decisions. USBX already uses the system bus after P3.4b, so P3.3 validates the resolved XHCI path without placing it in generated DSL. The EFI builder consumes the ACPI processor's dynamic output list, so P3.3 and P3.4d needed no edits to the EFI manifest or its builder. P3.5 was limited to cleanup because the existing campaign matcher enforces the BIOS binding. P4.3 added the required capability list to the profile and its strict reader, and both profile edits used campaign-only revision bumps.

No review chunk was stopped or skipped. The EFI iGPU allow-list includes `8086:5916`, but the component catalog classifies it as HD 620 and the current guided campaign requires UHD 620. The allow-list governs EFI generation and the campaign governs guided qualification. P3.2, P4.2, Phase 5 and later, and the owner steps remain deferred.

### 2026-11-17 review of the follow-up hand-back

Reviewed `adfb7f4` to `c905013` against P3.3, P3.4a, P3.4d, P3.5, P4.3 and P4.6. All six chunks are accepted. The review commit fixed one bug:

- **P3.3 tokenizer:** iasl's disassembler writes a comment inside well-known `_HID` calls, for example `EisaId ("PNP0C09") /* Embedded Controller Device */`. The new regular expression did not allow it, so the EC was never found on real firmware. `macloader/build/dsl_names.py` now allows block comments in a `Name` call, and a test covers it.
- **`8086:5916`:** kept. Some T480s units have 7th-generation HD 620 graphics. P4.3 already stops them, with a clear message, before the EFI check runs. The notes are under P3.3 and P4.6 in the plan.

After the fix the full suite had 778 passed, 17 skipped and 1 xfailed, with exit 0. Mypy passed for 89 source files.

Every chunk that can be done without the owner is now done. The next steps need the owner's Phase 0 facts:

- P0.2 unblocks P4.2.
- P0.3 unblocks P3.2.
- P0.4 unblocks Phase 5.

P0.5a is reserved for the reviewer.
