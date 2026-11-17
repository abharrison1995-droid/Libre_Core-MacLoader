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
7. **P4.3, P4.4, P4.5, P4.6**: kext set from the profile, kext order, structural validation, iGPU check and stale profile data. P4.3 and P4.6 edit `macloader/database/data/profiles/t480s/p4-sequoia.yaml`: bump the profile revision and the campaign `revision` as the plan says.

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
