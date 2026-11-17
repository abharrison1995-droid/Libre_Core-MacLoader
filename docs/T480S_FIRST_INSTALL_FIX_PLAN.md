# T480s first-install fix and improvement plan

Written 2026-11-16 against checkout `d00714e` (`main`) from a read-only review by 16 scoped reviewers, with the main findings re-checked against the code by the orchestrator; revised the same day after two reviews of the plan itself. **This is the single active plan for the project.** The older implementation plans, handoffs and review stubs were removed on 2026-11-17; their history is in Git.

Target: ThinkPad T480s 20L8, BIOS N22ET85W 1.62, prepared from a Windows 11 AMD64 host that is the T480s itself, ending in a first macOS installation.

## Progress (updated 2026-11-17)

| Item | State | Where |
|---|---|---|
| P0.1 baseline | Done | Result under P0.1 |
| P0.6 green baseline | Done, merged | `299d1bb` |
| Phase 1 (P1.1-P1.7) | Done, merged, **pushed** | `origin/main` at `78bdc4b`; checkpoint note above Phase 2 |
| Phase 2 (P2.1, P2.2, P2.2b, P2.3-P2.6) | Done, merged and pushed | `4d0ad32`; checkpoint note at the end of Phase 2 |
| P2.7 flaky guided tests | Done, merged to local `main` | `plan/P2.7`; Result at the end of Phase 2 |
| P0.2-P0.5 (owner at the machine) | Not started | Phase 0 |
| P0.5a (orchestrator confirms BIOS table) | Not started | Under P0.5 |
| P3.1 Windows capture | Done, merged to local `main` | `plan/P3.1` |
| P3.2 SSDT count | Deferred to owner input | Requires P0.3 |
| P3.3 generated SSDT resolution | Stopped | Recording the PNLF omission in the build manifest requires out-of-scope files |
| P3.4a PLUG | Skipped | Requires the CPU path from P3.3 |
| P3.4b USBX | Done, merged to local `main` | `plan/P3.4b` |
| P3.4c PNLF | Done, merged to local `main` | `plan/P3.4c` |
| P3.4d EC | Skipped | Requires the EC path from P3.3 |
| P3.5 ACPI BIOS binding and cleanup | Stopped | Import already checks the normalized BIOS |
| P4.1 HFS+ driver | Done, merged to local `main` | `plan/P4.1` |
| P4.2 explicit config | Deferred to owner input | Requires P0.2 |
| P4.3 profile kext set | Stopped | The reviewed profile has no revision to bump |
| P4.4 kext order | **Next chunk** | Phase 4 |
| Phases 5-11 | Not started | |

**P2.7 checkpoint:** 753 passed, 16 skipped, 1 xfailed (the deliberate USB-keyboard resume case from P2.2b), 0 failed in each of two full runs. Mypy passed for all 88 source files.

**Improvements logged while implementing** (Phase 12 table): I21 typed WWAN field, I22 PM981-from-Windows and UASP disks, I23 Linux removable disks and `usb_count`, I24 test hygiene (a suite test runs a live probe).

**Handover:** the next implementing agent starts from [HANDOVER.md](HANDOVER.md), which lists the chunks that can be done without the owner and how to hand the work back for review.

### How each chunk is run (orchestrator protocol)

A single agent working alone plays both roles below; [HANDOVER.md](HANDOVER.md) section 3 spells that out.

1. The orchestrator launches one worker agent (Opus 5.5, medium effort) per chunk, with a prompt that names the chunk, the files it may edit, the current baseline and any context from earlier chunks. Only one chunk is in flight at a time.
2. The worker branches `plan/<chunk-id>` from `main`, works, commits there, and reports in the format of rule 12. Workers never push or merge.
3. The orchestrator reads the diff, re-runs the full suite (`-q -rf`) and mypy itself, and only then fast-forwards `main`. Check the pytest exit status separately: a `| tail` pipe hides a failure.
4. When a worker stops at a decision point, the orchestrator decides, records the decision in this file next to the chunk, and either resumes the same worker or writes a follow-up chunk (as with P2.2b).
5. At the end of each phase: a checkpoint note in this file, then the owner decides whether to push `main`.

---

## 0. How to use this plan

### 0.1 Rules for every worker

1. **One chunk per session.** Do one chunk (for example `P1.2`), then stop and report. Do not start the next chunk.
2. **Read first.** Every chunk has a "Read first" list. Read those lines before editing. Line numbers are from `d00714e`; if earlier chunks moved them, find the quoted code by searching.
3. **Evidence tags.** `[C]` = the orchestrator confirmed the finding in the code. `[R]` = a reviewer reported it and it was not re-checked. For every `[R]` item, confirm it in the code before changing anything. If it is not true, write that in your report and skip that item.
4. **Stop instead of improvising.** If the code does not match what the chunk describes, or the fix needs a file outside the chunk's "Files" list, stop and report. Do not widen the chunk.
5. **Never run these** (they touch hardware, identity or the network in ways that need the owner present): `macloader usb ...`, `macloader identity generate`, `macloader autoload` (any mode), `macloader recovery download`, `python -m macloader.removable.windows_qualification --run`, any `diskpart`, `Format-*`, `Clear-Disk`, `Set-Disk`.
6. **Allowed commands:** `python -m pip install -e ".[dev]"`, `python -m pytest ...`, `python -m mypy macloader` (on this host `python` is not on PATH: always write `.\.venv\Scripts\python.exe -m ...` from the repo root), `git status`, `git diff`, `git switch -c plan/<chunk-id>`, `git add`, `git commit` on the chunk's branch. Never `git push`, `git merge`, `git rebase` or `git reset`.
7. **Private data never enters the repo.** No serial numbers, UUIDs, MAC addresses, ACPI tables, SMBIOS values or `C:\Users\<name>` paths in code, tests, fixtures, docs or commit messages. Fixtures use invented values.
8. **Tests are part of the chunk.** Each chunk lists tests to add. A chunk without its tests is not done.
9. **Verification for every chunk:** run the chunk's listed test files, then `python -m pytest -q -rf` (whole suite; `-rf` names any failure) and `python -m mypy macloader`. On Windows, write commit messages to a temp file and use `git commit -F <file>`; PowerShell 5.1 mangles multi-line `-m` strings. Report the pass/fail counts exactly. Do not report "done" with failing tests.
10. **Policy data changes.** If a chunk edits a YAML file under `macloader/database/data/` that has a `policy_version`, `revision` or `*_policy_id` field, the chunk text says how to bump it. Loader validation in `macloader/database/campaigns.py:53-153` cross-checks these; run `tests/unit/test_reference_campaigns.py` and `tests/unit/test_database_loader.py` after any data edit.
11. **Writer backend files are digest-bound.** `macloader/removable/windows_native.py`, `windows_image.py`, `windows_qualification.py`, `adapters.py`, `writer.py` feed the approval digest (`windows_qualification.py:23`). All edits to them belong in Phase 7 and must be finished before Phase 8. Never edit them after Phase 8 starts.
12. **Report format:** chunk id; files changed; what was confirmed for each `[R]` item; tests added; exact test and mypy results; anything skipped and why.
13. **Branches and merging.** Each chunk gets its own branch `plan/<chunk-id>` (for example `plan/P1.2`), created from the current tip of `main`. The worker commits there and stops. Only the orchestrator or the owner merges into `main`. Most chunks edit the same few files (`autoloader/service.py`, `detection/windows.py`), so **only one chunk is in flight at a time**; the next chunk starts from `main` after the previous one is merged. The only exception is chunks marked "independent".
14. **Split chunks.** Where a chunk says "Split:", each lettered part (for example `P7.3a`) is its own chunk, session, branch and report.
15. **If the baseline is already red.** If `python -m pytest -q` fails before you change anything, stop and report; do not fix unrelated failures inside your chunk.

### 0.2 Severity and order

Phases run in the order given in "Path to the first install" at the end of this file (it is not purely numeric: Phase 9, the Phase 10 code and data chunks, and P11.1/P11.2 all land **before** Phase 8). Inside a phase, chunks run in order unless marked "independent". Phases 1 to 9 are fixes needed to reach a bootable, safely written USB. Phase 10 is the install phase. Phases 11 and 12 are improvements.

**Phase checkpoint.** At the end of every phase the orchestrator runs the full suite and mypy on `main`, reads the phase's combined diff, and only then releases the next phase.

**Why the order matters.** Session artifacts and the smoke-boot checkpoints are bound to the campaign digest (`macloader/autoloader/service.py:208`, `macloader/autoloader/first_boot.py:35`). Any change to campaign, profile or evidence policy data after the owner's real `autoload` run forces a rebuild, a rewrite of the USB and a repeat of the smoke boot. So every chunk that edits policy data or adds a stage must be merged before the owner's real run.

### 0.3 Owner decisions this plan assumes

The plan is written for the recommended option. If the owner picks differently, the affected phase must be re-planned before work starts.

| Id | Decision | Assumed | Affects |
|---|---|---|---|
| D1 | USB port mapping from Windows | Build a Windows provider (Phase 5). Fallback: one Linux live session (P5.5). | Phase 5 |
| D2 | Recovery target | Accept the Sequoia build Apple serves; record the real build after boot. Exact 24A335 stays unproven. | Phase 6, Phase 10 |
| D3 | Internal disk | **Decided by the owner 2026-11-16: erase the whole internal disk.** Windows, MacLoader and the private workspace on this laptop are destroyed by the install, so a recovery host and the exports in P10.2 are mandatory. The keep-Windows text in Phase 10 is kept only as a documented alternative; workers write the runbook with "erase" as the main path. | Phase 10 |
| D4 | Is the Windows host the T480s itself? | Yes (recent commits are Windows detection fixes). Consequence: once the laptop is in Recovery, MacLoader is not running, and if the disk is erased MacLoader, the private workspace and the identity are gone (see P10.2, P10.4). | Phase 0, Phase 10 |
| D5 | SSDT count | Whatever a fresh capture on this BIOS shows (P0.3), stored as policy data. | Phase 3 |

### 0.4 Chunks that need a stronger reviewer

Chunks marked **REVIEW** change boot-critical or destructive behaviour. After the worker finishes, the diff must be reviewed by the orchestrator model or the owner before merge. Workers still do the work; they just must not merge it themselves.

### 0.5 What the owner needs on hand

**Decided 2026-11-16: two sticks, not four.** The earlier draft asked for an installer, a spare installer, a Linux live USB and a separate backup medium. That is consolidated to:

- **Installer stick:** one USB 3 stick, 16 to 64 GB, holding nothing of value. Used for writer qualification first, then written as the installer. MacLoader erases it.
- **Rescue stick:** one USB stick, 32 GB or larger, that is both the Linux live USB and the backup medium. It is made with a multi-boot tool that leaves the stick as an ordinary drive (the owner copies a Linux ISO file onto it and it becomes bootable; the remaining space is a normal folder area readable from Windows and Linux). It holds, in a folder named `MacLoader-rescue`: the identity export, a full copy of the installer stick's contents, the repository at the qualified commit, the Python wheels, and the firmware before/after record. MacLoader never writes to it, and it is unplugged whenever the installer stick is being written.
  - The multi-boot tool is third-party software the owner downloads and checks themselves; no worker downloads or runs it. If the owner prefers not to use one, the fallback is two plain sticks (a normal Linux live USB and a normal data stick) with the same folder on the data stick. Nothing else in the plan changes.
  - The rescue stick carries the private identity. Keep it physically safe, and delete the identity export from it once the install is accepted.
- **No spare installer stick.** The full copy on the rescue stick replaces it: restoring is a file copy back onto the installer stick. A spare is optional insurance against the stick physically dying; if the owner has a third stick, any 16 GB stick can receive the copy later.
- A genuine USB 2.0 device (an old stick, mouse or keyboard) for the high-speed half of the port checks in Phase 5.
- An Ethernet cable and a wired network. Recovery downloads macOS (about 14 GB) and Intel Wi-Fi does not work there.
- A second way to read instructions while the laptop is in Recovery: a printout of the runbook's install section, or a phone.
- **A recovery host (mandatory, D3 is "erase"):** the Linux live system on the rescue stick, able to run MacLoader again if the EFI must be rebuilt. It can rebuild the EFI but **cannot write a stick through MacLoader**: the guided writer is only ever qualified on Windows (`macloader/autoloader/media.py:40` [C]). It must be tested before the install: MacLoader installs on it and `macloader identity import` works there. **Decided by the owner 2026-11-16: a Linux live USB booted on the T480s itself.** See P10.7 for the rehearsal.
- **Optional, only if anything on the Windows disk matters:** the owner's own external drive for a Windows backup and a Windows installer to return to Windows later. This is not one of the two sticks and MacLoader does not need it. The BitLocker recovery key is saved to the rescue stick either way, because P0.5 can trigger a recovery prompt before the erase.

Wherever this plan says "second medium", it means the `MacLoader-rescue` folder on the rescue stick.

---

## Phase 0 — Ground truth (no code changes)

These produce facts later phases depend on. P0.2 to P0.5 need the owner at the T480s.

**Run order: P0.1, P0.2, P0.5, P0.3, P0.4.** The firmware settings (P0.5) must be at their final values before the ACPI capture (P0.3): settings such as Thunderbolt BIOS Assist, SGX or disabling a device can change which firmware tables exist, which would invalidate the capture, the SSDT count stored in P3.2 and the USB evidence. After P0.5, **do not change firmware settings again** until after the install; if one must change, repeat P0.3 and everything that depends on it.

### P0.1 Baseline test run
- **Do:** `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`, then `.\.venv\Scripts\python.exe -m pytest -q` and `.\.venv\Scripts\python.exe -m mypy macloader`.
- **Record** in the report: test counts, coverage figure, any failures or skips on Windows. Do not fix anything.
- **Done when:** the baseline numbers are written down. Later chunks compare against them.
- **Result (2026-11-17, `d00714e`, confirmed by the orchestrator):**
  - **Interpreter:** `python` and `py` are not on PATH on this host. Every `python -m ...` command in this plan must be run as `.\.venv\Scripts\python.exe -m ...` from the repo root (Python 3.12).
  - **pytest:** 659 passed, 2 failed, 14 skipped, about 2 minutes. No coverage figure is printed by the default run.
  - **The 2 failures** are both `OSError [WinError 1314]` from `Path.symlink_to` on unprivileged Windows: `tests/unit/test_p4_toolchain.py::test_toolchain_root_and_version_checks_reject_untrusted_state` and `tests/unit/test_removable_adapters.py::test_linux_discovery_uses_stable_by_id_and_rejects_system_internal_and_mounted_disks`.
  - **mypy:** 2 errors in 2 files: `macloader/ui/tui.py:228` [dict-item] and `macloader/ui/guided.py:122` [override].
  - P0.6 makes this baseline green. Until P0.6 is merged, exactly these four items are the known baseline and are not a reason to stop under rule 15; anything else red is.

### P0.6 Make the baseline green (worker)
- **Why:** later workers must be able to report "all green" without judgement calls.
- **Read first:** the two failing tests; how the six existing symlink tests skip (for example `tests/unit/test_acpi_capture.py:50`, `tests/unit/test_p4_acpi.py:68`); `macloader/ui/tui.py:215-235`; `macloader/ui/guided.py:115-130`.
- **Change:**
  1. In the two failing tests, apply the same skip-when-symlinks-are-unavailable guard the other symlink tests already use (reuse the existing helper or pattern; do not invent a new one). Only the symlink-dependent part may be skipped; if the test has assertions that do not need a symlink, keep them running by splitting the test.
  2. Fix the two mypy errors without changing behaviour and without `# type: ignore` unless there is no typed alternative (if you use one, say why in the report). For `guided.py:122`, make `action_quit` match the Textual base signature (an `async def`) and check every caller still works. For `tui.py:228`, correct the mapping's value type so worker-decorated methods are accepted.
- **Files:** the two test files, `macloader/ui/tui.py`, `macloader/ui/guided.py`. Nothing else.
- **Done when:** `pytest -q` shows 0 failed (the two tests pass or skip with the symlink reason), `mypy macloader` shows no errors, and `tests/unit/test_guided.py` and `tests/unit/test_tui.py` still pass.
- **Result (2026-11-17, merged to local `main` as `299d1bb`, confirmed by the orchestrator): done.** `tests/unit/test_guided.py` was added to the allowed files; the quit logic now lives in `GuidedApp._request_quit()` and `action_quit` is async. **New baseline for every later chunk: 661 passed, 16 skipped, 0 failed; mypy "Success: no issues found in 88 source files".** Anything red from here on is a reason to stop under rule 15.
- **Second intermittent failure, now identified:** `tests/unit/test_guided.py::test_retry_reprobes_only_for_hardware_and_binding_blockers` (added in P2.4) failed in one of six runs of `tests/unit/test_guided.py` on 2026-11-17. P2.7 fixes both intermittent tests. Until then, if a worker sees only these failures, rerun once and report them by id; a second failure in a row is a stop.
- **Known flaky test (seen once during P2.1):** `tests/unit/test_readiness_repairs.py::test_tui_apply_target_is_clickable_at_supported_small_sizes` failed once in a full run and passed alone and on the rerun. If it is the only failure, rerun the suite once; if it passes, report it as flaky and carry on. If it fails twice, stop.

### P0.2 Inspect the pinned Sample.plist defaults
- **Why:** `macloader/build/config.py:226-269` [C] copies OpenCore's `Sample.plist` and overrides only a few keys. Every quirk and security setting is the sample default. Phase 4 needs the real defaults.
- **Do:** after the owner runs `macloader toolchain install`, find `Sample.plist` in the per-user workspace toolchain folder (search for the file name; `macloader/build/config.py:140-159` shows how it is located). Read it with Python `plistlib`.
- **Record** these values exactly: `Booter.Quirks` (all keys), `Kernel.Quirks` (all keys), `Misc.Security` (all keys), `Misc.Boot` (all keys), `Misc.Debug` (all keys), `NVRAM.Add`, `NVRAM.Delete`, `NVRAM.WriteFlash`, `UEFI.Quirks`, `UEFI.APFS`, `UEFI.ConnectDrivers`, `PlatformInfo.Generic` keys, the top-level `PlatformInfo` flags.
- **Also record** the number of entries, and how many have `Enabled` true, in each sample array the builder does not replace: `Misc.Tools`, `Misc.Entries`, `Kernel.Patch`, `Kernel.Block`, `Kernel.Force`, `Booter.Patch`, `Booter.MmioWhitelist`, `ACPI.Delete`, `UEFI.ReservedMemory`.
- **Output:** a table in the report. No repo change.

### P0.3 Fresh ACPI capture count (owner, elevated PowerShell)
- **Why:** the code requires exactly one DSDT and eleven SSDTs (`macloader/build/acpi.py:26`, `290-292`). `docs/RESEARCH_LEDGER.md:409` says twelve SSDTs; `docs/IMPLEMENTATION_LEDGER.md:62` says eleven [C].
- **Do:** owner runs `tools/capture_t480s_followup.ps1` from an elevated PowerShell with the output directory set **outside the repo**.
- **Record:** number of DSDT and SSDT files, their file names, and the exit code of the dump tool. Do not paste table contents.
- **Also record** (owner reads the disassembled DSDT privately): does `\_PR.PR00` exist or is it `\_PR.CPU0`; the full path of the EC device and its name; the name of the XHCI controller device; whether a `PNLF` device already exists; the iGPU device name (`GFX0` or `VID`).

### P0.4 Windows device dump (owner, normal PowerShell)
- **Why:** several detection findings depend on what Windows really lists on this laptop.
- **Do:** owner runs these read-only commands and saves output outside the repo:
  - `Get-CimInstance Win32_PnPEntity | Where-Object { $_.Class -in 'Net','Bluetooth','MEDIA','HIDClass','Keyboard','Mouse','USB','SCSIAdapter' } | Select-Object Name,PNPDeviceID,Class,Service | ConvertTo-Json`
  - `Get-CimInstance Win32_DiskDrive | Select-Object Model,InterfaceType,PNPDeviceID,Size | ConvertTo-Json`
  - `Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'PCI\*' } | Get-PnpDeviceProperty -KeyName DEVPKEY_Device_BusNumber,DEVPKEY_Device_Address | Select-Object InstanceId,KeyName,Data | ConvertTo-Json`
  - With one USB stick in the left USB-A port: `Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\*' } | Get-PnpDeviceProperty -KeyName DEVPKEY_Device_LocationPaths | Select-Object InstanceId,Data | ConvertTo-Json`
- **Then a worker** turns the output into a sanitized fixture `tests/fixtures/t480s/t480s_20l8_windows_live_shape.json`: keep `Name`, `Class`, `Service`, vendor and device IDs and the row structure; replace every serial-like suffix in `PNPDeviceID` with `REDACTED`; drop `Size`. This fixture is used by Phase 1 and Phase 5 tests.
- **Privacy, allow-list not redaction:** Bluetooth rows for paired devices carry device names (often a person's name) and MAC addresses. Keep only rows whose `PNPDeviceID` starts with `PCI\`, `HDAUDIO\`, `ACPI\`, `SCSI\`, `SWD\MSRRAS\`, `USB\VID_` or the Microsoft virtual-adapter prefixes needed by the P1.1 tests. Drop every row starting `BTHENUM\`, `BTHLE\`, `BTHLEDEVICE\` or `BTH\` except one invented `BTH\MS_RFCOMM\...` row. If in doubt, drop the row and invent a replacement.

### P0.5 Firmware settings: record, then set (owner) — run before P0.3
- **Orchestrator step first (P0.5a, not a worker chunk):** the orchestrator checks every row of the table below against a public T480/T480s OpenCore reference, corrects the table in this file, and records the source URL per row. The owner does not change any setting until P0.5a is marked done here.
- **Record first:** follow `docs/T480S_FIRST_INSTALL_RUNBOOK.md:43-47`: photograph and note every current value. These are the rollback values.
- **Then set** the target values below. Each is the owner's decision made at the machine. The orchestrator confirms each row against a public T480/T480s OpenCore reference before the owner applies it and records the source URL for P10.1. Menu names are from memory of this BIOS family and may differ slightly; match by setting name.

  | Menu | Setting | Value |
  |---|---|---|
  | Security → Secure Boot | Secure Boot | Disabled |
  | Startup | UEFI/Legacy Boot | UEFI Only |
  | Startup | CSM Support | No |
  | Config → Storage | Controller Mode | AHCI |
  | Security → Memory Protection | Execution Prevention | Enabled |
  | Security → Virtualization | Intel VT-d | either (the config sets `DisableIoMapper`) |
  | Security → Virtualization | Kernel DMA Protection | Disabled |
  | Security | Device Guard | Disabled |
  | Security → I/O Port Access | Wireless WAN | Disabled |
  | Config → Thunderbolt | BIOS Assist Mode | Disabled |
  | Security | Intel SGX | Disabled |

- **Do not** disable or clear the Security Chip (TPM) while Windows uses BitLocker: that locks the Windows volume. Before changing Secure Boot, save the BitLocker recovery key off the machine (see P10.2); Windows may ask for it on the next boot.
- **Output:** the before/after value of each row, kept outside the repo. Needed for P10.1.

---

## Phase 1 — Windows hardware detection

All chunks: main file `macloader/detection/windows.py`; tests in `tests/unit/test_windows_provider.py`. Every chunk adds at least one test that feeds rows through **both** the detector and `CompatibilityEngine` (existing tests stop at the detector, which is why these bugs were missed).

### P1.1 Ignore software network and Bluetooth rows [C]
- **Problem:** `windows.py:395-401` adds any row that has "bluetooth" in its text or `Class == Net`, even with no PCI or USB ID. `macloader/compatibility/engine.py:196-216` and `219-243` then report each as `unknown-ethernet` / `unknown-wifi`, which blocks the plan (`macloader/configuration/service.py:119-120` [R]).
- **Read first:** `windows.py:372-423`, `engine.py:194-268`.
- **Change:** in the `bluetooth` branch and the `net` branch, only append when `pnp_pci is not None or usb is not None`. Rows without either are skipped. Do not change the engine.
- **Tests:** rows "WAN Miniport (IP)" (`Class` Net, `PNPDeviceID` `SWD\MSRRAS\...`), "Microsoft Wi-Fi Direct Virtual Adapter" (`{5D624F94-...}\VWIFIMP_WFD\...`), "Bluetooth Device (RFCOMM Protocol TDI)" (`BTH\MS_RFCOMM\...`) produce no ethernet, wifi or bluetooth entries; the real I219 (`PCI\VEN_8086&DEV_15D7...`), 8265 (`PCI\VEN_8086&DEV_24FD...`) and BT (`USB\VID_8087&PID_0A2B...`) still do; engine result for the combined fixture has no `unknown-*` component.
- **Done when:** tests pass and the P0.4 fixture yields zero unknown network components.

### P1.2 WWAN card is declared, not unknown [R]
- **Problem:** the reference laptop has a Fibocom L830-EB WWAN on USB (`docs/T480S_REFERENCE_EVIDENCE_2026-09-10.md:20`, `44`). If Windows lists it as `Class` Net it becomes `unknown-ethernet`.
- **Read first:** `windows.py:395-401`; `macloader/domain/hardware.py` `NetworkInfo`; `engine.py:194-216`; `macloader/database/data/evidence/t480s.yaml:28` (HS06 excluded).
- **Change:** before the ethernet/wifi split, if the lowercased row text matches the regex `wwan|mobile broadband|fibocom|l830|\blte\b|sierra wireless` (the word boundary matters: plain `lte` matches "fi**lte**r", which appears in Windows network row names), append `NetworkInfo(kind="wwan", ...)` to a new `wwan` list on the snapshot instead of ethernet. If `HardwareSnapshot` has no `wwan` field, **stop and report** (adding a snapshot field changes digests and needs its own chunk). In the engine, a `wwan` entry adds a warning string "WWAN is not supported and is left unmapped" and no component result.
- **Orchestrator decision (2026-11-17), replaces the "Change" above:** the worker confirmed `HardwareSnapshot` has no `wwan` field, and that snapshot digests cover the whole `to_dict()` output. No snapshot field is added before the first install. Instead, the Windows detector **drops** WWAN rows: before the ethernet/wifi split, a row matching the regex is skipped (not added to ethernet, wifi or bluetooth; its USB device entry in `usb_devices` is left as it is). The match must run before the "wireless" token test, because "Sierra Wireless" otherwise lands in wifi. No engine change, no warning. Files: `macloader/detection/windows.py`, `tests/unit/test_windows_provider.py` only. The typed `wwan` field with a visible warning is deferred to Phase 12 as I21.
- **Test data:** P0.5 disables the WWAN card before the P0.4 dump, so the real fixture will not contain it. Use an invented row.
- **Tests:** a Fibocom USB Net row gives one wwan entry, zero ethernet entries, and a warning, not an unknown.

### P1.3 Drop Intel Display Audio from the audio list [C]
- **Problem:** `windows.py:406-414`: for a row named "Intel(R) Display Audio", `is_codec` is false, so it is stored with `pci=pnp_pci` (8086:280b). No audio component matches, giving `unknown-audio` [R for the engine half].
- **Change:** if `pnp.upper().startswith("HDAUDIO")` and "display audio" is in the lowercased name, `continue` (skip the row). Leave real codecs unchanged.
- **Tests:** an `HDAUDIO\FUNC_01&VEN_8086&DEV_280B...` row named "Intel(R) Display Audio" yields no audio entry; the ALC257 row (`VEN_10EC&DEV_0257&SUBSYS_17AA2258`) still yields codec `10ec:0257`, subsystem `17aa:2258`; engine reports ALC257 and no unknown audio.

### P1.4 Storage classification and removable-disk exclusion [C]
- **Problem A:** `windows.py:435` calls a disk NVMe only if the model or interface text contains `nvme`, `pm981` or `mzvlb`. The reference drive is "SSSTC CA5-8D256-HP" and Windows usually reports `InterfaceType` `SCSI` for NVMe, so it becomes `unknown`.
- **Problem B:** `windows.py:425-443` lists every disk, including USB sticks. `macloader/configuration/observations.py:37` puts all disks into `storage.identity`, so plugging in the installer stick changes the machine's hardware facts (see P2.1).
- **Read first:** `windows.py:424-443`, `observations.py:20-50`, `engine.py:469-489`.
- **Change:**
  1. Skip any disk whose `InterfaceType` is `USB` (case-insensitive) or whose `PNPDeviceID` starts with `USBSTOR\`. These are not part of the laptop.
  2. Set `kind = "nvme"` when `PNPDeviceID` upper-cased contains `VEN_NVME` or `\NVME` , or the existing tokens match. Keep the SATA rule; add `IDE` to the SATA interface set.
- **Tests:** disk `SCSI\DISK&VEN_NVME&PROD_SSSTC_CA5-8D256\...` with `InterfaceType` SCSI is `nvme`; a `USBSTOR\DISK&VEN_SANDISK...` row is absent from `snapshot.storage`; PM981 `MZVLB256HAHQ` is still `nvme` and still triggers the `nvme_compatibility_fix` capability in the engine.

### P1.5 Input and touch classification [C]
- **Problem:** `windows.py:415-416` matches only the single words `touchscreen` / `touchpad`, and tests `keyboard` before `trackpoint`.
- **Change:** replace the branch with regex tests on `lower`, in this order: touchscreen = `touch\s*-?\s*screen`; trackpoint = `track\s*-?\s*point`; trackpad = `touch\s*-?\s*pad|track\s*-?\s*pad|clickpad|synaptics|elan`; keyboard = `keyboard`; mouse-only rows (`mouse` and none of the above) get `kind="mouse"` and must **not** be labelled trackpad. If `InputDeviceInfo.kind` does not allow `mouse`, skip mouse-only rows.
- **Read first:** `windows.py:415-417`, `windows.py:474` (touch capability), `engine.py:592-606`.
- **Tests:** "HID-compliant touch screen" gives `touchscreen` and `touch_capability=True`; "ThinkPad TrackPoint" style rows give `trackpoint`; "HID-compliant mouse" alone does not create a trackpad; a fixture with keyboard + trackpoint still yields the `ps2_trackpad_trackpoint` capability.

### P1.6 PCI slot for Windows devices [C] — REVIEW
- **Problem:** `PciDevice.pci_slot` is set only by the Linux detector (`macloader/detection/linux.py:265`, `291`, `303`). `macloader/build/efi.py:314` and `macloader/build/usb_map.py:44` need the USB controller's slot, so an EFI build from a Windows snapshot always fails.
- **Read first:** `windows.py:375-423`; `macloader/domain/hardware.py:10-52`; `usb_map.py:43-45`; the P0.4 bus-number/address output.
- **Change:** add one extra query returning `InstanceId`, `DEVPKEY_Device_BusNumber` and `DEVPKEY_Device_Address` for present `PCI\*` devices. Do **not** parse `DEVPKEY_Device_LocationInfo`: it is a translated sentence and differs on non-English Windows. Both values are integers. `device = address >> 16`, `function = address & 0xFFFF`. Format as `0000:BB:DD.F`: bus and device as two lower-case hex digits, function as one digit, domain always `0000`. Refuse (leave `None`) when bus > 255, device > 31 or function > 7. Build a dict from upper-cased `InstanceId` to slot and set `pci_slot` on each `PciDevice` created from a matching `PNPDeviceID`. If the query fails or a device lacks either value, leave `pci_slot=None` (the existing fail-closed behaviour) and record the query failure in the provider's query status.
- **Tests:** bus 0, address `0x00140000` → `0000:00:14.0`; bus 0, address `0x001F0003` → `0000:00:1f.3`; bus 2, address 0 → `0000:02:00.0`; missing address → `None`; a fixture with the XHCI controller yields `usb_controllers[0].pci_slot == "0000:00:14.0"`.
- **Done when:** format matches what `linux.py` produces for the same device (compare with `tests/fixtures/t480s/t480s_20l8_162_synthetic.json`).

### P1.7 Per-category completeness and visible query failures [R]
- **Problem:** one `pnp_ok` flag marks five categories complete (`windows.py:511-515`); a failed query is logged at debug only (`windows.py:65-90`), so the user sees "unknown" with no reason; a failed computer-system query becomes "unsupported manufacturer" (`windows.py:285-290`, `macloader/compatibility/model_matcher.py:21-28`).
- **Change:** (1) raise the PnP query timeout from 15 s to 45 s and retry once on timeout; (2) when a query fails, store a short reason (no paths, no raw output) in the existing query-status structure and surface it in the compatibility report's warnings; (3) when the computer-system query fails, set manufacturer to `None`, and make `match_model` return `UNKNOWN` (not `BLOCKED`) for a `None` manufacturer.
- **Tests:** simulated timeout then success works; simulated double failure yields a warning naming the query; `None` manufacturer gives `UNKNOWN`. Update `tests/unit/test_windows_provider.py:296` which currently accepts `BLOCKED`.

---

**Phase 1 checkpoint (2026-11-17, orchestrator):** P1.1-P1.7 merged to local `main` at `78bdc4b` (not pushed). Full suite 715 passed, 16 skipped, 0 failed; mypy clean. Decisions taken during the phase: P1.2 reduced to dropping WWAN rows (typed field deferred to I21); P1.4 PM981-from-Windows and UASP gaps deferred to I22; P1.5 skips generic mouse rows and requires a pointing-device row for Synaptics/ELAN names; P1.7 uses `""` (not `None`) for an unread manufacturer. Still open: the real-fixture checks that wait on P0.4.

## Phase 2 — Session engine stability

Main file `macloader/autoloader/service.py`; tests in `tests/unit/test_autoloader.py`, `test_autoloader_media.py`, `test_autoloader_store.py`.

### P2.1 Removable devices must not change machine identity [C] — REVIEW
- **Problem:** `service.py:379` compares full `hardware_facts` of a fresh probe against the launch snapshot before writing. With P1.4 the Windows side no longer lists USB disks, but USB devices and the Linux provider can still change facts.
- **Read first:** `service.py:373-387`, `service.py:100-127`, `macloader/configuration/observations.py:14-50` and `111-123`.
- **Change:** add a function `machine_facts(snapshot)` in `observations.py` that returns only: machine type, BIOS binding, cpu identity, igpu identity, audio codec + subsystem, ethernet identity, wifi identity, bluetooth identity, internal storage identity. It must exclude `usb_devices`, `usb_controllers` port contents and anything removable. Use `machine_facts` instead of `hardware_facts` at `service.py:379`. Do not change `hardware_facts` or the evidence scope digests.
- **Tests:** launch snapshot without a USB stick, write-time snapshot with one extra USB device → write is allowed; changed BIOS version → still `HARDWARE_CHANGED`; changed Wi-Fi card → still `HARDWARE_CHANGED`.
- **Orchestrator correction (2026-11-17), from the worker's stop report:** `hardware_facts` has no `usb_devices` key, so a USB stick alone never tripped this gate. The real removable trigger on Windows is a USB input device (an external USB keyboard, or anything else `_input_kind` classifies) changing `input.topology`. `machine_facts` leaves input out, so the change above still fixes it; the tests target that case instead. `machine_facts` keeps `machine.manufacturer` and `machine.product` (fixed for the machine). Linux USB disks also change `storage.identity`, but the guided write is Windows-only (`macloader/autoloader/media.py:40`), so this gate is never reached on Linux; that is deferred to I23.

### P2.2 Resume must not discard work when only removable devices changed [R]
- **Problem:** `service.py:120-121` keeps artifacts only when `input_digest` equals the configuration's `semantic_digest`; reviewers report the digest moves when a USB stick is attached at relaunch.
- **Do first:** write a failing test: create a session with `tools`, `efi` and `recovery` artifacts, relaunch with a snapshot that differs only by one extra `UsbDevice`, assert the artifacts survive. If the test already passes after P1.4 and P2.1, record that and stop (no code change).
- **If it fails:** find which field of the semantic digest changes (print both digests' inputs in the test) and report it. Do not change digest composition in this chunk; that needs a follow-up chunk written by the orchestrator.

- **Result (2026-11-17, merged as `dea25a9`):** confirmed. Both removable cases drop every artifact on resume; the tests are in `tests/unit/test_autoloader.py` as `xfail(strict=True)`. Cause: `UserConfiguration.hardware_snapshot_digest` hashes the whole sanitized snapshot (`macloader/configuration/observations.py:138-143`, mirrored by `ConfigurationService._snapshot_digest`, `macloader/configuration/service.py:281-285`), so an extra `usb_devices` entry changes `semantic_digest`, which the artifact filter compares (`macloader/autoloader/service.py:120-121`, `552`). A USB keyboard additionally changes the `input.topology` observation.

### P2.2b Snapshot digest ignores USB devices [C] — REVIEW
- **Decision:** fix the installer-stick case only. Nothing outside detection reads `snapshot.usb_devices` (repo-wide search), so it can safely leave the stored digest.
- **Change:** add one helper, for example `stable_snapshot_digest(snapshot)` in `macloader/configuration/observations.py`: `canonical_json_digest` of `snapshot.to_dict()` with `snapshot_id`, `timestamp` **and `usb_devices`** removed. Use it at both sites (`reconcile_configuration` and `ConfigurationService._snapshot_digest`), so `evaluate()` and the build plan's `hardware_content_digest` check (`macloader/build/efi.py:312`) stay consistent. Do not change `hardware_facts`, `scope_digest`, `EVIDENCE_SCOPES` or `input.topology`.
- **USB keyboard case: not fixed.** Filtering USB-bus input devices would also drop the internal touchscreen (a USB HID device) and would change the acpi evidence scope. Keep that test as `xfail(strict=True)` with the reason updated to "external USB input devices change input.topology; not fixed before first install". The runbook (P11.4a) says: do not plug in an external USB keyboard or mouse while a MacLoader session is in progress; if one was plugged in, MacLoader rebuilds its artifacts on the next start (no data loss, only time).
- **Tests:** remove the `xfail` from the extra-`UsbDevice` resume test (it must now pass); a test that the two digest sites give the same value for the same snapshot; a test that adding a `UsbDevice` leaves the digest unchanged while changing a Wi-Fi card changes it.
- **Files:** `macloader/configuration/observations.py`, `macloader/configuration/service.py`, `tests/unit/test_autoloader.py`, one existing configuration test file.

### P2.3 Compare-and-swap rollback [C]
- **Problem:** `service.py:540-545` and `service.py:123-125` increment `session.revision` before `store.save`. If the save raises, the in-memory revision stays advanced and the next save passes the check, overwriting another process's state.
- **Change:** in both places, wrap the save: on any exception set `self.session.revision = previous` (or `session.revision = previous`) and re-raise.
- **Tests:** a store whose `save` raises once: after the failure `revision == previous`; a second writer's state is not overwritten on retry (second save raises the revision-conflict error again).

### P2.4 Retry re-probes [R]
- **Problem:** `macloader/ui/guided.py:143-151` clears `_blocker` and restarts the advance loop, but `start()` only runs when `service.session is None`, so `HARDWARE_UNKNOWN` / `HARDWARE_MISMATCH` never clear without restarting the app.
- **Change:** add `AutoloaderService.reprobe()` that re-runs the probe-and-reconcile part of `start()` for the existing session (reuse the code at `service.py:74-127`; extract a helper, do not duplicate). In `guided.py` retry handler, call `reprobe()` when the current action code is one of `HARDWARE_UNKNOWN`, `HARDWARE_MISMATCH`, `CAMPAIGN_NOT_FOUND`, `SESSION_BINDING_AMBIGUOUS`.
- **Tests:** blocker `HARDWARE_UNKNOWN` with a first snapshot missing Wi-Fi; after `reprobe()` with a complete snapshot the next action is not `HARDWARE_UNKNOWN`.

### P2.5 Interrupted write stays visible after relaunch [R]
- **Problem:** `service.py:381-387`: a `media_attempt` artifact is recorded before writing, but `_calculate_next_action` (`service.py:138-218`) never looks at it. After a failed or killed write, relaunch shows a normal USB selection.
- **Change:** in `_calculate_next_action`, before the `self.media.qualified` check at line 212: if `"media_attempt" in artifacts and "media_write" not in artifacts`, return a `HUMAN` action, code `MEDIA_ATTEMPT_UNRESOLVED`, message "A previous USB write did not finish. That USB may be partly erased and must not be booted.", choices `("I will reuse or discard that USB; start again",)`. Choosing it removes the `media_attempt` artifact and saves.
- **Also:** in `_write_media`, a refusal raised **before** `mark_started` runs must not use the "may have been erased" wording (`service.py:602-606` [R]). Track a local flag set inside `mark_started`.
- **Tests:** relaunch with `media_attempt` only → `MEDIA_ATTEMPT_UNRESOLVED`; pre-write refusal message does not contain "erased".

### P2.6 Clear error codes for binding and corrupt sessions [R]
- **Problem:** `service.py:84-90` maps every `ValueError` to `SESSION_BINDING_AMBIGUOUS`; one corrupt session file blocks everything (`macloader/autoloader/store.py:41-49`); unknown keys raise `TypeError`.
- **Change:** in the store's discovery loop, catch `(ValueError, TypeError, KeyError)` per file, skip that file, and collect its name. Return distinct codes: `MACHINE_IDENTITY_UNAVAILABLE` (no usable UUID/serial, raised at `service.py:69`), `SESSION_FILE_UNREADABLE` (one or more skipped files, shown as a warning, not a block, when another valid session matches), `SESSION_BINDING_AMBIGUOUS` (only for genuinely multiple matches).
- **Tests:** one corrupt file plus one valid matching session → session loads; no UUID and no serial → `MACHINE_IDENTITY_UNAVAILABLE`.

---

- **Result (2026-11-17, merged as `4d0ad32`):** all three claims confirmed (a `TypeError` from a malformed session file even escaped `start()`). New codes `MACHINE_IDENTITY_UNAVAILABLE` and `SESSION_FILE_UNREADABLE` (blocking only when no valid session matches and a skipped file may be this machine's; otherwise a warning prefixed to the next action's message); `SESSION_BINDING_AMBIGUOUS` now only for genuine multiple matches. Both new codes were added to the guided `REPROBE_CODES`.

**Phase 2 checkpoint (2026-11-17, orchestrator):** P2.1-P2.6 and P2.2b merged to local `main` at `4d0ad32`, **not pushed**. Full suite 753 passed, 16 skipped, 1 xfailed; mypy clean. Decisions taken during the phase: P2.1 re-scoped (the real removable trigger is a USB input device, not `usb_devices`); P2.2 split, with P2.2b removing `usb_devices` from the stored snapshot digest and the USB-keyboard resume case left as a strict xfail plus a runbook note; P2.5 part (b) was not a real problem in the service (the related guided-screen wording moved to P7.5). Open: P2.7.

### P2.7 Make the two intermittent guided-UI tests deterministic
- **Why:** two tests fail intermittently, so a red suite no longer reliably means a broken change:
  - `tests/unit/test_guided.py::test_retry_reprobes_only_for_hardware_and_binding_blockers` (about 1 in 6 runs of that file);
  - `tests/unit/test_readiness_repairs.py::test_tui_apply_target_is_clickable_at_supported_small_sizes` (seen once in a full run).
- **Do first:** run each test about 20 times in a loop and record the failure rate and the exact assertion or error. Read each test and find the race (typical causes in Textual pilot tests: asserting before `await pilot.pause()` or before `app.workers.wait_for_complete()`, clicking a widget before it is mounted or enabled, or depending on wall-clock timing).
- **Change:** fix the tests, not the production code, by waiting on the right condition. If the race is in production code (for example a busy flag cleared before a worker finishes), stop and report with the evidence instead of changing it.
- **Files:** `tests/unit/test_guided.py`, `tests/unit/test_readiness_repairs.py`.
- **Done when:** each test passes 30 runs in a row, and the full suite passes twice in a row.
- **Result** (`plan/P2.7`): Retry failed in 1 of 20 runs on `assert not app._busy` for `CONFIGURATION_BLOCKED`. The test now waits for the Retry handler to register its worker before waiting for workers to finish. Apply target passed 20 of 20 baseline runs. Its test now waits for layout refresh before clicking and verifies that the click increments the draft revision at each size. Both tests passed 30 consecutive runs after the changes. The two test files passed with 60 passed and 2 skipped. Both full runs finished with 753 passed, 16 skipped and 1 xfailed. Mypy passed for 88 source files.

## Phase 3 — ACPI capture and generated tables

### P3.1 Windows capture accepts a valid set regardless of exit code [C]
- **Problem:** `macloader/evidence/acpi_capture.py:155-156` fails on any non-zero exit; the reference note records exit `-1` after a complete dump [R: `docs/T480S_REFERENCE_EVIDENCE_2026-09-10.md:40`]. Line 152 runs `-b` only, which also dumps non-DSDT/SSDT tables and trips the exact-set check.
- **Read first:** `acpi_capture.py:135-163`; `tools/capture_t480s_followup.ps1:150-170` (the working invocation uses `-b -n DSDT` then `-b -n SSDT`).
- **Change:** run the tool twice in `self.destination`: `[tool, "-b", "-n", "DSDT"]` then `[tool, "-b", "-n", "SSDT"]`. Do not raise on a non-zero return code. Keep the cancel checks. After both runs, call the existing `_find_tables` / double-read / `validate_capture`. If `_find_tables` raises **and** either return code was non-zero, raise `CaptureError("ACPI_PERMISSION", "The capture tool could not read the firmware tables. Run MacLoader from an elevated (Administrator) terminal and retry.")`. If it raises with both return codes zero, let the existing table-set error surface.
- **Tests** (`tests/unit/test_acpi_capture.py`): update the fake tool at lines 103-108 so it (a) honours `-n`, (b) can exit `-1` after writing files, (c) writes extra `facp.dat` / `apic.dat` when called without `-n`. Cases: exit -1 with full set → accepted; exit -1 with no files → `ACPI_PERMISSION`; exit 0 with wrong count → table-set error.
- **Result** (`plan/P3.1`): Confirmed the reference exit of -1 and the separate DSDT/SSDT calls in the follow-up script. Windows capture now checks the table set after both filtered calls and preserves cancellation between them. Synthetic tests covered all three exit/set cases. Targeted tests 16 passed, 1 skipped. Full suite 755 passed, 16 skipped, 1 xfailed. Mypy passed for 88 source files.

### P3.2 SSDT count is policy data, not a constant [C] — REVIEW
- **Problem:** `macloader/build/acpi.py:26` and `290-292` hard-code eleven.
- **Needs:** the count from P0.3.
- **Change:** add `expected_ssdt_count: <N from P0.3>` under the ACPI section of `macloader/database/data/evidence/t480s.yaml`; bump that file's policy id suffix (for example `2026.09-a` → `2026.11-a`) and update the matching `evidence_policy_id` in `macloader/database/data/campaigns/t480s-20l8-sequoia-15.0.yaml` plus the campaign `revision` (+1). Make `TABLE_NAMES` a function of the count (`dsdt.dat`, `ssdt.dat`, `ssdt1.dat` … `ssdt{N-1}.dat`; confirm naming against P0.3 file names). Thread the count from the loaded evidence policy to `_find_tables` and `validate_capture`. Schema: validate `1 <= count <= 32`.
- **Tests:** count 11 and count 12 both validate with matching fixtures; a mismatch says "expected N SSDTs, found M".
- **Docs:** fix the contradiction between `docs/RESEARCH_LEDGER.md:409` and `docs/IMPLEMENTATION_LEDGER.md:62` by adding one dated line stating the P0.3 result.

### P3.3 Generated SSDTs must resolve against the captured DSDT [C] — REVIEW
- **Problem:** `macloader/build/acpi.py:429-473` emits fixed source using `\_PR.PR00` and `\_SB.PCI0.XHC` without checking the captured tables. `External()` lets it compile even when the object does not exist.
- **Read first:** `acpi.py:116-235` (disassembly and build), `acpi.py:429-473`, and `macloader/evidence/usb_capture.py:31-66` (an existing parser that already tracks `Device`/`Scope` nesting to build full namespace paths).
- **Do not write a second DSL parser.** Move the nesting-aware tokenizer from `usb_capture.py:36-66` into a shared helper (same module or a new `macloader/build/dsl_names.py`) that yields `(full_path, kind, attributes)` for each `Device`/`Scope`/`Processor`, and make `usb_capture.py` call it so its existing tests still pass. If that refactor is more than about 60 lines, stop and report.
- **Change:** after disassembly, add `_resolve_names(dsl_dir) -> dict` built on that helper. It returns: `cpu0` (`\_PR.PR00` or `\_PR.CPU0`, or `\_SB.PR00`), `xhc` (the device **directly under `\_SB.PCI0`** whose `_ADR` is `0x00140000`), `ec` (full path of the device with `_HID` `PNP0C09`), `igpu` (the device **directly under `\_SB.PCI0`** whose `_ADR` is `0x00020000`), `has_pnlf` (bool). More than one match for any name is an error, not a pick-the-first. If `cpu0` or `xhc` is not found, raise `BuildPlanError("Captured firmware does not define <name>; generated ACPI cannot be bound")`. Pass the resolved names into `_generated_sources` instead of the literals. If `has_pnlf` is true, omit SSDT-PNLF and record that in the manifest.
- **Tests** (`tests/unit/test_p4_acpi.py`): DSL fixture with `PR00` → source uses `\_PR.PR00`; fixture with `CPU0` → uses `\_PR.CPU0`; fixture without an XHCI device → `BuildPlanError`; fixture with existing PNLF → no PNLF table.
- **Result** (`plan/P3.3`): Stopped before editing code. The fixed names and existing tokenizer match the plan. The generated ACPI processor writes a qualification report, while the EFI build manifest is constructed in `macloader/build/efi.py` and parsed against the strict field set in `macloader/domain/contracts.py`. The required manifest record for an existing PNLF needs a change outside this chunk's scope. The plan needs to specify the qualification report or authorize the manifest changes. No implementation tests were run.

### P3.4 Make the generated tables do what they claim [C] — REVIEW
- **Problem:** SSDT-PLUG is `Name (PLUG, One)` with no `_DSM`; SSDT-USBX has no properties (`acpi.py:433-471`). PNLF uses `_CID "APP0002"` and `_UID 0x13` [R: differs from the Dortania reference].
- **Split:** `P3.4a` PLUG, `P3.4b` USBX, `P3.4c` PNLF, `P3.4d` EC. Each part updates the expected-file assertions it affects.
- **Change:** replace the three bodies (names resolved by P3.3):
  - **PLUG:** inside `Scope (<cpu0>)`, define `Method (_DSM, 4, NotSerialized)` that returns `Buffer (One) { 0x03 }` when `Arg2 == Zero`, otherwise `Package (0x02) { "plugin-type", One }`. Guard the scope with `If (_OSI ("Darwin"))`.
  - **USBX:** `Scope (\_SB)` → `Device (USBX)` with `Name (_ADR, Zero)`, a `_DSM` returning (for non-zero `Arg2`) `Package (0x08) { "kUSBSleepPowerSupply", 0x13EC, "kUSBSleepPortCurrentLimit", 0x0834, "kUSBWakePowerSupply", 0x13EC, "kUSBWakePortCurrentLimit", 0x0834 }`, and `Method (_STA)` returning `0x0F` under Darwin else `Zero`.
  - **PNLF:** `_HID` `EisaId ("APP0002")`, `_CID` `"backlight"`, `_STA` `0x0B` under Darwin else `Zero`. Set `_UID` to `0x10` (16): the profile uses a Kaby Lake framebuffer (platform-id `0000C087`, device-id `16590000`), and `0x13` is the Coffee Lake value. The reviewer confirms 16 against the WhateverGreen `SSDT-PNLF` source comments before merge. A wrong value gives a wrong brightness range, not a boot failure.
  - **EC:** if `ec` resolved in P3.3 has a device name other than `EC`, add `SSDT-EC-T480S`: `Scope (\_SB)` → `Device (EC)` with `_HID "ACID0001"` and `_STA` `0x0F` under Darwin else `Zero`. If the firmware device is already named `EC`, generate nothing and record "native EC present" in the manifest.
- **Also update:** the expected output file list wherever it is asserted (`tests/unit/test_p4_acpi.py:143`, `macloader/build/efi.py:363-369`).
- **Tests:** generated source contains `_DSM` and `plugin-type`; USBX contains the four property names; EC table is emitted only when the firmware name is not `EC`. Real compilation is covered by P11.2.
- **Result** (`plan/P3.4a`): Skipped. PLUG still uses a fixed processor path because P3.3 stopped. The specified Darwin-guarded replacement takes the resolved `cpu0` path. No code changed or implementation tests run.
- **Result** (`plan/P3.4b`): USBX now defines the four power properties under the system bus with Darwin-only device status. The zero-function `_DSM` query reports function support. This scope needs no resolved device path. The output filename is unchanged and the EFI builder already uses the processor's output list. Source tests covered the scope, methods and property values. Targeted tests 5 passed, 1 skipped. Full suite 756 passed, 16 skipped, 1 xfailed. Mypy passed for 88 source files.
- **Result** (`plan/P3.4c`): Confirmed the current PNLF source and Kaby Lake profile values. The [OpenCore PNLF sample referenced by WhateverGreen](https://github.com/acidanthera/OpenCorePkg/blob/master/Docs/AcpiSamples/Source/SSDT-PNLF.dsl) confirms UID 16 for Skylake/Kaby Lake and UID 19 for Coffee Lake. PNLF now has the reviewed IDs and Darwin-only status. This body change is independent of P3.3's omission rule. The source test covers the IDs and status. Targeted tests 6 passed, 1 skipped. Full suite 757 passed, 16 skipped, 1 xfailed. Mypy passed for 88 source files.
- **Result** (`plan/P3.4d`): Skipped. The generator has no resolved `ec` path because P3.3 stopped. The EC emission decision requires that path. The native EC outcome also needs a manifest record. No code changed or implementation tests run.

### P3.5 Bind captured tables to the live machine and clean intermediates [R]
- **Problem:** `macloader/workflow/service.py:126-134` takes `bios_binding` from the campaign profile and never compares against the current probe; decompiled DSL persists under the work root (`acpi.py:128-135`).
- **Change:** (1) at import, require the current snapshot's normalised BIOS to equal the profile's `bios_binding`, else raise with "This capture can only be imported on BIOS <binding>". (2) After a successful or failed build, delete the `disassembled` directory and iasl logs in a `finally`; keep only digests.
- **Tests:** import with snapshot BIOS `N22ET76W-1.53` fails; work directory has no `.dsl` after build.
- **Result** (`plan/P3.5`): Stopped before editing code. `import_acpi_capture` already requires `candidate_campaign(snapshot)`, which compares the normalized snapshot BIOS with the campaign BIOS. The campaign loader requires the profile to have the same BIOS, and `test_machine_bound_acpi_import_rejects_wrong_bios_or_corrupt_capture` already covers rejection. The disassembly and raw logs still persist. The plan needs a separate cleanup chunk or a revised import requirement. No implementation tests were run.

---

## Phase 4 — EFI content that boots

### P4.1 Add an HFS+ driver [C] — REVIEW
- **Problem:** only `OpenRuntime.efi` and `ResetNvramEntry.efi` ship (`macloader/database/data/dependencies/catalog.yaml:14-19`). OpenCore cannot open the Recovery `BaseSystem.dmg` without an HFS+ driver.
- **Read first:** `catalog.yaml:1-40` (opencore entry), `macloader/build/efi.py:465-470` and `706-738`, `macloader/build/config.py:191-192`, `232-233`.
- **Change:** add `EFI/OC/Drivers/OpenHfsPlus.efi` to the opencore `subcomponents` list (it is inside the same OpenCore release archive, so the archive hash does not change). Do **not** use `HfsPlus.efi` (that file is in a different repository and is not in the pinned archive). In `config.py`, emit drivers with `OpenRuntime.efi` first, then the rest sorted. In `validate_tree`, require both `OpenRuntime.efi` and `OpenHfsPlus.efi` to exist and be listed in `UEFI.Drivers`.
- **Do not add** `ApfsDriverLoader.efi`; OpenCore loads APFS itself.
- **Tests:** generated `UEFI.Drivers` paths are `["OpenRuntime.efi", "OpenHfsPlus.efi", "ResetNvramEntry.efi"]` in that order; validation fails when `OpenHfsPlus.efi` is missing.
- **Result** (`plan/P4.1`): Added the HFS+ driver supplied by the [OpenCore 1.0.7 packaging script](https://github.com/acidanthera/OpenCorePkg/blob/1.0.7/build_oc.tool#L1375). Driver generation puts OpenRuntime first and sorts the rest. Structural validation requires both boot drivers on disk and in `UEFI.Drivers`. Updated synthetic EFI fixtures and tested ordered output, extraction, and missing files or entries. Targeted and data-loader tests 97 passed, 3 skipped. Full suite 761 passed, 16 skipped, 1 xfailed. Mypy passed for 88 source files.

### P4.2 Explicit reviewed config settings [C] — REVIEW
- **Problem:** `config.py:226-269` leaves every quirk and security key at the sample default and replaces boot-args with `alcid=<n>` only.
- **Needs:** the P0.2 table.
- **Change (data):** add a `config_overrides` block to `macloader/database/data/profiles/t480s/p4-sequoia.yaml`, then bump the profile revision and the campaign `revision`. Values (source: Dortania OpenCore Install Guide, Kaby Lake laptop page; the reviewer confirms each against P0.2 and that page):

  | Section | Key | Value |
  |---|---|---|
  | Booter.Quirks | AvoidRuntimeDefrag | true |
  | Booter.Quirks | EnableSafeModeSlide | true |
  | Booter.Quirks | EnableWriteUnprotector | true |
  | Booter.Quirks | ProvideCustomSlide | true |
  | Booter.Quirks | SetupVirtualMap | true |
  | Booter.Quirks | RebuildAppleMemoryMap | false |
  | Booter.Quirks | SyncRuntimePermissions | false |
  | Kernel.Quirks | AppleXcpmCfgLock | true |
  | Kernel.Quirks | DisableIoMapper | true |
  | Kernel.Quirks | DisableLinkeditJettison | true |
  | Kernel.Quirks | PanicNoKextDump | true |
  | Kernel.Quirks | PowerTimeoutKernelPanic | true |
  | Kernel.Quirks | XhciPortLimit | false |
  | Kernel.Quirks | CustomSMBIOSGuid | false |
  | Misc.Security | Vault | "Optional" |
  | Misc.Security | ScanPolicy | 0 |
  | Misc.Security | SecureBootModel | "Default" |
  | Misc.Security | AllowSetDefault | true |
  | Misc.Security | BlacklistAppleUpdate | true |
  | Misc.Boot | ShowPicker | true |
  | Misc.Boot | HideAuxiliary | false |
  | Misc.Boot | PickerMode | "Builtin" |
  | Misc.Boot | Timeout | 0 |
  | Misc.Debug | AppleDebug | true |
  | Misc.Debug | ApplePanic | true |
  | Misc.Debug | DisableWatchDog | true |
  | Misc.Debug | Target | 67 |
  | NVRAM.Add (7C436110-…) | boot-args | "-v keepsyms=1 debug=0x100 alcid=<layout>" |
  | NVRAM.Add (7C436110-…) | csr-active-config | data 00000000 |
  | NVRAM.Add (7C436110-…) | prev-lang:kbd | data "en-US:0" (ASCII bytes) |
  | NVRAM | WriteFlash | true |
  | UEFI | ConnectDrivers | true |
  | UEFI.Quirks | RequestBootVarRouting | true |
  | UEFI.Quirks | ReleaseUsbOwnership | true |
  | UEFI.APFS | EnableJumpstart | true |
  | UEFI.APFS | MinDate | 0 |
  | UEFI.APFS | MinVersion | 0 |

- **Change (code):** extend `ReviewedEfiProfile` (find the dataclass used by `config.py`) with `config_overrides`; in `generate()`, after loading the sample, apply every override by section path. If a section or key from the overrides does not exist in the sample schema, raise `BuildPlanError` naming it (this catches typos and OpenCore version drift). **Exception:** keys under `NVRAM.Add.<GUID>` are free-form; there, only the GUID dictionary must already exist. After the overrides, set these sample arrays to empty lists so no sample entry survives: `Misc.Tools`, `Misc.Entries`, `Kernel.Patch`, `Kernel.Block`, `Kernel.Force`, `Booter.Patch`, `Booter.MmioWhitelist`, `ACPI.Delete`, `UEFI.ReservedMemory` (compare with the counts recorded in P0.2). Build `boot-args` from the override template with `<layout>` replaced. Ensure `boot-args`, `csr-active-config` and `prev-lang:kbd` are also present in `NVRAM.Delete` for the same GUID (so stale values are reset).
- **Tests** (`tests/unit/test_efi_builder.py` or a new `test_config_overrides.py`): every key in the table has the expected value and plist type in the generated config; an override naming a non-existent key raises; a new key under an existing `NVRAM.Add` GUID does not raise; the nine sample arrays are empty; `boot-args` contains `-v` and `alcid=86`.

### P4.3 Kext set comes from the reviewed profile [C] — REVIEW
- **Problem:** `macloader/compatibility/engine.py:529-606` adds WhateverGreen, VoodooPS2, AppleALC etc. only when detection found the device. A detection miss silently drops a kext needed to see or type in the installer.
- **Read first:** `engine.py:515-620`, `macloader/dependencies/resolver.py:86-200`, `macloader/database/data/profiles/t480s/baseline.yaml`, `macloader/configuration/policy.py:150-170`.
- **Change:** add `required_capabilities` to `p4-sequoia.yaml`: `accelerated_intel_uhd_620`, `alc257_audio`, `intel_gigabit_ethernet`, `intel_wireless_lan`, `intel_bluetooth`, `ps2_trackpad_trackpoint` (use the exact strings the engine emits; confirm each by search). In the place where the build plan for a reviewed profile is finalised (find where `ReviewedEfiProfile` and the BuildPlan meet; likely `macloader/workflow/service.py` or `macloader/build/efi.py:127-141`), compare the plan's capabilities with the profile's required list. Any missing capability raises a blocking issue `PROFILE_CAPABILITY_MISSING` naming the capability and the hardware category that was not detected. Do not auto-add the capability: the block is the fix.
- **Tests:** snapshot without an input device → `PROFILE_CAPABILITY_MISSING: ps2_trackpad_trackpoint`; complete snapshot → no issue.
- **Result** (`plan/P4.3`): Stopped before editing code. Confirmed all six capability names in the engine and resolver. `ConfigurationService.evaluate` is where the build plan and reviewed profile meet. Handover section 2 calls for a profile revision bump, but `p4-sequoia.yaml` has only `schema_version`; `ReviewedEfiProfile` has no revision and its loader rejects unknown keys. Campaign revision 3 is separate. The plan needs to define the initial profile revision and compatibility rules or specify a campaign-only revision bump. No implementation tests were run.

### P4.4 Kext load order [C/R] — REVIEW
- **Problem:** `config.py:172-188` orders by leaf name. Plug-ins nested under a parent (for example `VoodooPS2Controller.kext/Contents/PlugIns/VoodooInput.kext`) can sort before the parent [R]; BlueToolFixup sorts before IntelBluetoothFirmware [R].
- **Change:** rewrite `_ordered_kexts` as a stable sort with this key: (group, parent-first path). Groups: 0 `Lilu.kext`; 1 `VirtualSMC.kext`; 2 `WhateverGreen.kext`, `AppleALC.kext`; 3 `SMC*.kext`; 4 everything else **except** Bluetooth; 5 `IntelBluetoothFirmware.kext`; 6 `IntelBTPatcher.kext`; 7 `BlueToolFixup.kext`. Within a group sort by full bundle path, and enforce: any path that starts with `<parent>.kext/` comes after `<parent>.kext`. Add `lilu` to the `dependencies` of `intel_bluetooth_firmware` in `catalog.yaml` if it is empty [R `catalog.yaml:294-323`].
- **Tests:** ordering for the full T480s kext list: Lilu first; VoodooPS2Controller before every `VoodooPS2Controller.kext/Contents/PlugIns/*`; IntelBluetoothFirmware < IntelBTPatcher < BlueToolFixup.

### P4.5 Structural validation checks what OpenCore will load [R]
- **Problem:** `macloader/build/efi.py:706-738` checks kext directories exist but not `Info.plist`, not the executable named by `ExecutablePath` (`config.py:167` hard-codes `Contents/MacOS/<name>`), and not `ACPI.Add` files.
- **Change:** (1) in `_kernel_entry`, accept an `has_executable: bool` argument; set `ExecutablePath` to `""` when the extracted bundle has no `Contents/MacOS/<name>` file. Thread the flag from where bundles are extracted. (2) In `validate_tree`, for each `Kernel.Add` entry verify `PlistPath` exists and, when `ExecutablePath` is non-empty, that file exists; for each `ACPI.Add` entry verify the `.aml` exists; reject entries whose files are missing.
- **Tests:** codeless bundle → empty `ExecutablePath` and validation passes; wrong executable name → validation fails; missing AML → validation fails. Update fixtures in `tests/unit/test_efi_builder.py:107-110` so normal kexts include a `Contents/MacOS/<name>` file.

### P4.6 Graphics identity check and stale profile data [R]
- **Change:** before generation, require the captured iGPU PCI ID to be `8086:5917` or `8086:5916` (the IDs the `device-id` spoof `16590000` is valid for); otherwise raise `BuildPlanError`. Remove `HS06` from `usb.routes` in `p4-sequoia.yaml:24` (it is excluded in `evidence/t480s.yaml:28`). Reword `macloader/database/data/macos/sequoia.yaml:8` to "Intel Wi-Fi uses itlwm on Sequoia (not physically verified)".
- **Tests:** iGPU `8086:3ea0` → build refused.

---

## Phase 5 — USB port mapping from Windows (decision D1)

### P5.1 Spike: can Windows give the firmware route? (owner + worker, no code)
- **Do:** from the P0.4 `LocationPaths` output, check whether the USB stick's entry contains an ACPI path such as `ACPI(_SB_)#ACPI(PCI0)#ACPI(XHC_)#ACPI(RHUB)#ACPI(SS01)`. Repeat (owner) with the stick in each USB-A port and with a USB 2 device.
- **Report:** the exact strings (they contain no private data). If no `ACPI(...)` path appears on this firmware, skip P5.2 and P5.4 and go to P5.5. **P5.3 and P5.6 run whatever P5.1 finds:** the Linux fallback (P5.5) and the recovery host (P10.7) both need P5.3.

### P5.2 Windows USB event provider — REVIEW
- **Problem:** `macloader/autoloader/service.py:456-457` [C] refuses Windows; only `LinuxUsbEventProvider` exists (`macloader/evidence/usb_capture.py:68-101`).
- **Read first:** `usb_capture.py` in full (214 lines); `service.py:441-510`; `tests/unit/test_usb_capture.py`.
- **Change:** add `WindowsUsbEventProvider` in `usb_capture.py` with the same interface as the Linux provider. It polls present `USB\*` devices with their `DEVPKEY_Device_LocationPaths`, `DEVPKEY_Device_BusReportedDeviceDesc` and removable/hardwired state. For each device: take the `ACPI(...)` location path, convert it to a namespace path (`\_SB.PCI0.XHC.RHUB.SS01`) by joining the four-character segments and trimming trailing underscores; the last segment is the logical port; look the namespace up in the firmware `addresses` map (already produced by `collect_firmware_usb_addresses`). A device with no ACPI path, or whose namespace is not in the map, is **not reported** (no guessing). Speed: `5000Mbps` if the logical port starts with `SS`, `480Mbps` if it starts with `HS`. Identity is stored only as the same HMAC the Linux provider uses.
- **Do not** remove the `win32` refusal yet; that happens in P5.4.
- **Tests:** injected PowerShell JSON for (a) stick on `SS01`, (b) USB 2 device on `HS01`, (c) device with no ACPI path → ignored, (d) namespace not in the address map → ignored.

### P5.3 Canonical evidence scope digests across providers [R]
- **Problem:** scope digests for `cpu.identity`, `input.topology` and `usb_controllers` hash provider-specific fields (`macloader/configuration/observations.py:14-17`, `42-49`), so evidence captured under one provider is stale under the other.
- **Change:** in `observations.py`, build those three scope inputs from canonical identity only: CPU vendor + brand string; input device kinds (sorted, unique); USB controller `vendor:device` IDs plus `pci_slot`. Bump nothing in YAML. Existing saved evidence becomes stale once; say so in the commit message.
- **Tests:** the same machine described by the Linux synthetic fixture and by the P0.4-shaped Windows fixture gives equal `scope_digest` for each of the three scopes.

### P5.4 Enable the Windows wizard — REVIEW
- **Split:** `P5.4a` provider selection (the first paragraph below, with the `win32` test); `P5.4b` removal gate (item 1); `P5.4c` discard choice (item 2); `P5.4d` optional USB-C steps (item 3, the only part that edits policy data).
- **Change:** in `service.py:456-466`, replace the `win32` raise with provider selection: `WindowsUsbEventProvider` on Windows, `LinuxUsbEventProvider` otherwise. Keep the synthetic-fixture refusal.
- **Also fix in the same area** (all [R], confirm first): (1) `usb_capture.py:153-158` removal gate: ignore devices that were present when the wizard started, and name the blocking device's description in the instruction; (2) `service.py:504-506` + `usb_capture.py:150-151`: add a "Discard port checks and start again" choice that deletes the wizard progress file; (3) `macloader/database/data/evidence/t480s.yaml:34-45`: mark the eight USB-C steps `optional: true` and let the wizard offer "Skip this USB-C step" which records the existing "logical USB-C correlation unresolved" state (bump the evidence policy id and campaign revision as in P3.2).
- **Tests:** add the missing `win32` path test next to `tests/unit/test_usb_capture.py:197`; skip-USB-C yields PARTIAL evidence that `macloader/build/usb_map.py:28` accepts; discard resets the cursor.

### P5.5 Fallback: Linux live session procedure (docs only; only if P5.1 fails)
- **Write** a runbook section: boot a Linux live USB on the T480s, install MacLoader, run the guided flow through the USB stage, export the configuration and private evidence, import on Windows. Requires P5.3 so the evidence is not stale. State plainly that the Engineering JSON import accepts self-attested data and that the PowerShell scripts in `tools/` cannot produce it (`macloader/evidence/usb.py:131` requires `schema_version` "1"; the scripts write "0.3") [R].

### P5.6 Honest map metadata [R]
- **Change:** in `macloader/build/efi.py:381`, `400`, record `generated_usb.first_install_label` from the map (computed at `usb_map.py:61-62`) instead of the profile's route; raise if it is empty. Unify the controller-name rule: make `usb_capture.py:53` and `usb_map.py:52` both accept `XHC` and `XHCI` via one shared helper.
- **Tests:** manifest label equals the observed physical label; `XHCI`-named namespace passes both checks.

---

## Phase 6 — Recovery acquisition (decision D2)

### P6.1 Session bootstrap must be a GET [C]
- **Problem:** `macloader/recovery/discovery.py:238-239` passes `data or b""`; `urllib` sends a POST for any non-`None` body. Likely cause of the recorded HTTPS 405.
- **Change:** pass `data` unchanged (`None` when there is no body) to the transport; make the transport type accept `Optional[bytes]`. At `discovery.py:134` nothing else changes.
- **Tests** (`tests/unit/test_recovery_discovery.py`): the stub transport records `(url, data)`; assert `data is None` for `/` and bytes for `/InstallationPayload/RecoveryImage`.

### P6.2 Smoke metadata over HTTPS [C] — REVIEW
- **Problem:** `macloader/recovery/smoke.py:80` rewrites `https://` to `http://`.
- **Change:** request the `https://` URL as given. Keep `_NoMetadataRedirects`. If the owner's first real run fails over HTTPS, record the exact error in the report and stop; do not restore HTTP without an owner decision. Update `macloader/database/data/recovery/smoke.yaml:5` and the message at `service.py:201` if they mention "legacy metadata"/HTTP; bump `smoke_recovery_policy_id` and the campaign revision.
- **Tests:** stub `build_opener` and assert the request URL scheme is `https`.

### P6.3 Stall timeouts instead of total deadlines [C]
- **Problem:** `macloader/recovery/acquirer.py:95`, `196`, `269-270` cap each asset at 60 s total; `macloader/dependencies/downloader.py:58`, `108-109` cap at 30 s total.
- **Change (both files, same design):** replace the single total deadline with (a) a per-read idle timeout of 60 s (reset after every successful chunk read) and (b) a generous ceiling of `max(600, size_bytes / 50_000)` seconds (50 kB/s floor). Keep cancellation checks. In `downloader.py`, add up to 3 attempts with `Range` resume when the server supports it; add `except ArtifactDownloadError: raise` before the generic `except Exception` at lines 139-140 so real reasons are not relabelled "Unexpected error" [R].
- **Also** [R]: `acquirer.py:213-241`: when an existing partial already has the full expected size, verify its hash instead of sending a `Range` request (which returns 416).
- **Tests:** a slow stream that delivers a chunk every 5 s for 100 s succeeds; a stream silent for 61 s fails with a "stalled" message; full-size partial is verified without a network call. Use a fake clock; do not sleep in tests.

### P6.4 Tell the truth about the Recovery build [R] — REVIEW
- **Problem:** the smoke record stores the policy target as if it described the image (`smoke.py:43`); the UI offers "Retry exact qualification" which cannot succeed (`service.py:198-201`, `discovery.py:199-206` always returns `AMBIGUOUS`); nothing controls which macOS Recovery installs (`discovery.py:173` `"os": "default"`).
- **Change:** (1) rename the smoke record field to `requested_target_unverified` (accept the old name when reading); (2) in `service.py:200`, when no exact path exists (always, today), offer only `("Smoke test only",)` and reword: "Apple does not publish a way to prove this image is build 24A335. The image is Apple-signed and fully verified, but its macOS build is unknown until it boots."; (3) the media label and first-boot instructions say "macOS Recovery (build unverified)".
- **Tests:** next action after a failed qualification attempt has exactly one choice; record round-trips both field names.

### P6.5 Real chunklist fixture [R]
- **Problem:** the Apple chunklist signature code (`acquirer.py:753-794`) is tested only against a synthetic fixture with a patched key.
- **Change:** add an opt-in test (`@pytest.mark.network`, skipped by default) that the owner can run once: download only the small `.chunklist` from the smoke path and verify its signature with the production key. Document the command in `docs/CI_POLICY.md`.

---

## Phase 7 — Writer and consent safety (finish before Phase 8)

Every chunk in this phase is **REVIEW**. These edits change the backend digest; that is expected and is why they come before qualification.

### P7.1 The user types the erase confirmation [C]
- **Problem:** `macloader/autoloader/media.py:109` and `macloader/removable/windows_qualification.py:192` call `DestructiveConfirmation.issue(plan)` in code. Nobody types anything. The phrase shows a hash, not the device.
- **Read first:** `macloader/removable/writer.py:219-251`, `media.py:74-126`, `service.py:214-218`, `macloader/ui/guided.py:86-140`.
- **Change:**
  1. `DestructiveConfirmation`: add `from_typed(plan, typed: str)` which builds the expected phrase `ERASE <MODEL> <last 4 of serial> <capacity in whole GB>GB` (upper-case, single spaces; capacity is `size_bytes // 10**9`; `<MODEL>` is the reported model with every character other than letters, digits and spaces removed and runs of spaces collapsed to one) and raises `UnsafeRemovableTarget` if `typed.strip().upper()` differs. Some sticks report no serial, or one shorter than four characters: in that case the middle token is `DISK<n>` (the Windows disk number, for example `DISK2`) and the message says the stick has no serial. If the model is empty use `USB`. Remove `issue()` from production code paths; tests build confirmations through `from_typed` with the computed phrase (add a test helper in `tests/`, not in the package).
  2. `GuidedMediaService.write` takes the typed string and calls `from_typed`.
  3. Autoloader: the `USB_ERASE_CONFIRMATION` action (`service.py:215`) becomes a text-input action. Message lists model, capacity, last 4 of serial, disk number, and any drive letters, then the exact phrase to type. Add an `input_required: bool` field to `NextAction` if none exists.
  4. `guided.py` and the text CLI (`macloader/ui/cli.py:77-102`): render an input box / prompt for `input_required` actions; default focus on "Choose another USB"; Enter with an empty box does nothing.
  5. `windows_qualification.py:146`: replace `click.confirm` with a `click.prompt` requiring the same typed phrase.
- **Tests:** wrong phrase → no write call; right phrase → write proceeds; phrase for two identical-model sticks differs by serial suffix; a stick with no serial gets a `DISK<n>` phrase that still works; guided screen for the erase action exposes no single-click erase button.

### P7.2 Eligibility limits [C]
- **Problem:** `macloader/removable/adapters.py:180` treats any USB-bus disk as removable, with no upper size bound (`writer.py:950-966`).
- **Change:** in `_assert_safe`, refuse devices larger than 256 GiB with "This device is larger than an installer USB (256 GB limit). External drives are never erased." In `adapters.py:171-186` and `media.py:84-96`, stop silently dropping devices: return `(device, reason)` for excluded ones and show the reasons under the "Find USB devices" result, including "no eligible USB found" when the list is empty.
- **Also** [R]: `adapters.py:370` reads `$disk.IsRemovable`, which `Get-Disk` may not provide. Keep the BusType rule as the authority and delete the `IsRemovable` read if P0.4 shows the property is absent.
- **Tests:** 2 TB USB disk refused with the size message; mounted stick listed as excluded with reason "has a mounted volume"; empty scan produces the explicit message.

### P7.3 Confirmation freshness and write ordering [R]
- **Split:** `P7.3a` item 1; `P7.3b` item 2 (report findings first; only change the order after the orchestrator agrees); `P7.3c` item 3; `P7.3d` item 4.
- **Change:** (1) `writer.py:554-563`: re-check `confirmation.valid_for(plan)` immediately before `self.destructive_write(...)`; (2) confirm in `windows_native.py` / `windows_image.py` that the GPT headers are written **after** the partition contents; if they are written first, change the stream order so an interrupted write leaves no valid GPT (report what you found before changing); (3) `adapters.py:201-216`: do not remount after a failed invalidation, and chain exceptions with `raise ... from` instead of a bare `raise` in `finally`; (4) Linux `adapters.py:546-551`: run `_invalidate_descriptor` in a `finally` even when unmount fails.
- **Tests:** expired confirmation after a slow snapshot is refused; simulated crash mid-write leaves no `EFI PART` signature at LBA 1 in the disposable image; failed invalidation does not remount.

### P7.4 Windows native robustness [R]
- **Split:** `P7.4a` item 1; `P7.4b` items 2 and 3; `P7.4c` item 4 (elevation); `P7.4d` the `windows_qualification.py:272-273` message.
- **Change in `windows_native.py`:** (1) lines 324-391: move the binding check inside `try/finally` in `invalidate`, `remount`, `safe_eject`; release each handle independently so one close failure does not skip the rest; (2) lines 73-104: include `ctypes.get_last_error()` (number only) in `UnsafeRemovableTarget` messages; (3) lines 61-64, 246-263: skip volumes that cannot be opened or report zero extents instead of aborting the lock; (4) add `is_elevated()` using `ctypes.windll.shell32.IsUserAnAdmin()` and call it at the start of `lock` and in `windows_qualification.py:121-129`, raising "Run from an elevated (Administrator) terminal".
- **Change in `windows_qualification.py:272-273`:** print the sanitized exception text instead of the fixed generic message.
- **Tests:** device gone during `invalidate` → handles released and a later `lock` works; non-elevated → clear message; empty card reader volume → lock still succeeds.

### P7.5 Guided write experience [R]
- **Change (`macloader/ui/guided.py`, `service.py`):** while `Stage.WRITE` is running show "Writing and verifying <label>. Do not unplug. This takes several minutes."; disable Pause (`c`) during `Stage.WRITE`; when Pause is pressed elsewhere show "Pausing after the current step…" and render the paused state instead of discarding the `CANCELLED` action (`guided.py:122-127`, `153-154`).
- **Also (found in P2.5):** `macloader/ui/guided.py` (about line 112) shows "The selected USB may have been erased" for any non-`CaptureError` failure whenever **any earlier** session action is marked "may-have-occurred". After one interrupted write, a later refusal that happens before the new write starts would still claim the USB was erased. Base the wording on the current attempt only (for example: the latest `media_attempt` exists without `media_write`, or the service's own failure message), not on the whole action history.
- **Tests** (`tests/unit/test_guided.py`): pilot test that `c` during the write stage does not set `_cancelled`; paused text is visible after pause; after an earlier interrupted write, a pre-write refusal does not say "erased".

### P7.6 One qualification report and a real approval check [R]
- **Split:** `P7.6a` items 1 and 2; `P7.6b` item 3; `P7.6c` item 4; `P7.6d` item 5; `P7.6e` item 6 (docs only). `P7.6d` must be the last code chunk of Phase 7.
- **Change:** (1) `windows_qualification.py:153-214`: scenario runs append to the same report file given by `--report` instead of requiring a new file per scenario; (2) lines 199-200: do not auto-mark `exact-target-revalidation`; require it to be recorded explicitly; (3) lines 221-239: refuse `--record-case` for injected scenarios unless that scenario's injection trigger is present in the report; (4) add `--summary` that prints the report's SHA-256 and a redacted summary; (5) lines 23-27: add `autoloader/media.py` to the digest file list; (6) extend `docs/WINDOWS_WRITER_QUALIFICATION.md` with a YAML approval-record template and the exact command for each of the 15 cases.
- **Tests:** two scenarios append to one file; `--summary` hash equals `hashlib.sha256` of the file; recording an injected case without its trigger is refused.

---

## Phase 8 — Physical writer qualification (owner; no code)

Pushing: workers never push. The orchestrator or the owner pushes `main` after each phase checkpoint, which is what makes hosted CI run.

Preconditions: Phases 1 to 7 and Phase 9 merged; the Phase 10 code and data chunks (P10.1, P10.4) merged; P11.1 and P11.2 merged and green, including the real `ocvalidate` and real `iasl` job; hosted CI green on that exact commit; no further edits to the five backend files or `autoloader/media.py`. After this point, no chunk may change campaign, profile or evidence policy data until the install is finished.

1. Owner follows `docs/WINDOWS_WRITER_QUALIFICATION.md` (as extended in P7.6) on the Windows host, elevated, with a **sacrificial** USB stick of 16 to 64 GB that holds nothing of value. Disconnect every other external drive first.
2. Complete all 15 cases into one report; run `--summary`.
3. A worker (not the owner's physical steps) opens a PR adding one record to `macloader/database/data/media/windows-writer-approvals.yaml` with `backend_sha256`, `windows_version`, `architecture: AMD64`, `reviewed: true`, `physical_report_sha256`, `review_url`, and the 15 cases. The owner reviews and merges it. A worker must never set `reviewed: true` on its own judgement.
4. Confirm: `python -m macloader.removable.windows_qualification` (no flags) prints `production_approved: true`.

---

## Phase 9 — Identity, privacy and command behaviour

### P9.1 Guided identity uses the typed phrase [C] — REVIEW
- **Problem:** `service.py:616-631` generates a real identity on one click; Engineering requires the phrase (`macloader/ui/tui.py:380`, `macloader/workflow/service.py:46`, `219`).
- **Change:** make the IDENTITY action `input_required` (the mechanism from P7.1) and route generation through `WorkflowService`'s phrase-checked method. When more than one stored identity exists, list all of them (short fingerprint = first 8 hex of the identity file digest) instead of hiding "Reuse existing" (`service.py:182`).
- **Tests:** wrong phrase → no identity created; two stored identities → both offered.

### P9.2 Identity backup and fake-identity guard [R]
- **Change:** add `macloader identity export ID --to FILE` and `identity import FILE` (private-permission file, same ACL helper as the store; refuse paths inside the repo). The export target will usually be the rescue stick, whose filesystem has no file permissions: when the ACL helper cannot apply there, still write the file and print one warning line ("This location cannot protect the file; keep the device physically safe"); do not fail. In `macloader/build/efi.py:327-330`, raise if `fake_identity` is given together with a reviewed profile.
- **Tests:** export/import round trip; fake identity + reviewed profile → `BuildPlanError`.

### P9.3 `probe` is sanitized by default [R]
- **Problem:** `macloader/ui/cli.py:109` defaults `--sanitize` to false.
- **Change:** default to sanitized; add `--raw-private` to opt out, with a one-line warning on stderr. Fix the misleading comment at `macloader/domain/hardware.py:335-336`.
- **Tests:** default output contains no serial or UUID from the fixture.

### P9.4 Exit codes and error surface [R]
- **Split:** `P9.4a` exit codes for the four commands; `P9.4b` moving the two service constructors inside their `try`; `P9.4c` the top-level handler in `macloader/app.py`.
- **Change (`cli.py`):** `autoload --readiness` (lines 69-76), `preflight` (547-576), `toolchain status` (532-544) and `config check` (327-345) exit 1 when the result is blocked / missing / has blocking issues, still printing the same payload. Move `AutoloaderService()` (line 60) and `WorkflowService()` in `preflight` inside their `try`. Add one top-level handler in `macloader/app.py` that turns `OSError`, `ValueError`, `KeyboardInterrupt` into a single line without filesystem paths (exit 1; 130 for Ctrl-C).
- **Tests:** blocked readiness → exit code 1; injected `OSError("C:\\Users\\x\\file")` → output has no path.

### P9.5 Acknowledgement text is shown and checked [R]
- **Problem:** `macloader/configuration/service.py:206-213`: `config check` never prints the exact warning text to acknowledge; `config acknowledge` accepts any string.
- **Change:** include `rule_id` and the exact warning text in each `ACKNOWLEDGEMENT_REQUIRED` issue printed by `config check`; make `config acknowledge` reject text whose digest does not match. Update `tests/unit/test_cli.py:392-396`, which currently relies on any string being accepted.

### P9.6 Support review target name [C]
- **Problem:** `macloader/workflow/service.py:549` passes `configuration.target.version` ("15.0") where the engine wants a product name.
- **Change:** pass `configuration.target.product_id`. Add an assertion on the outcome in `tests/unit/test_tui.py:91`.

---

## Phase 10 — The install phase (decisions D2, D3)

**Timing.** P10.1 and P10.4 change campaign data and the stage machine, so they are merged **before Phase 8** and before the owner's real `autoload` run (see section 0.2). P10.2, P10.3, P10.5 and P10.6 are documentation and can be written any time before the install.

### P10.1 BIOS prerequisites (docs + data) — REVIEW — before Phase 8
- **Problem:** the runbook only says to record BIOS settings (`docs/T480S_FIRST_INSTALL_RUNBOOK.md:43-47`); no target values exist anywhere.
- **Change:** copy the confirmed table from P0.5 (with its source URLs) into the runbook as "Required firmware settings", and add a matching `bios_prerequisites` list to the campaign YAML (data only; bump `revision`). The runbook states that these settings are applied **before** the ACPI capture and must not change afterwards, and repeats the BitLocker and Security Chip warnings from P0.5. Each row states the rollback value column (filled by the owner, not stored in the repo).
- **Guided flow:** before the ACPI evidence stage, add a human checkpoint listing the table with one choice "I have set and checked these settings". Place it before ACPI capture, not before `Stage.FIRST_BOOT`.
- **Tests:** the ACPI capture action is not offered until the checkpoint is confirmed; the checkpoint text contains every row of the campaign list.

### P10.2 Pre-install safety section (docs)
- **Write** in the runbook, before any Recovery disk action: full backup of the internal disk verified by restoring one file; BitLocker status (`manage-bde -status`) and recovery key saved off the machine, or BitLocker suspended; decision D3 recorded (erase the whole internal disk, or keep Windows — if keeping Windows, the owner must shrink the Windows partition from Windows first and must only ever erase the new empty partition); Windows recovery USB created; list of what is lost under each option.
- **Because the build host is the laptop being installed (D4), also require before leaving Windows:**
  1. `macloader identity export` (P9.2) to the second medium. Without it a rebuilt EFI would carry a different identity.
  2. A copy of **everything** on the installer stick (the `EFI` folder and the Recovery folder, with hidden files) to the rescue stick (P10.7). No spare installer stick is required.
  3. A named recovery host able to run MacLoader again if the stick must be rebuilt: Windows itself under "keep Windows"; another PC or a Linux live USB under "erase". Under "erase" with no recovery host, the runbook says: do not proceed.
  3b. The Windows backup and Windows recovery USB listed above are **optional** under the decided erase path: required only if the owner wants anything from Windows or wants a way back to it. The runbook asks the question explicitly ("Is there anything on this disk you need?") and records the answer; it does not demand a backup nobody wants.
  4. The install section printed or open on a phone; the laptop cannot show it while in Recovery.
- **Keep-Windows specifics:** shrink Windows to leave at least 80 GB unallocated (Recovery downloads about 14 GB and the installed system needs the rest; on a 256 GB disk that is tight, so check Windows free space first). After the install, start Windows with F12 → Windows Boot Manager, not through the OpenCore picker: booting it through OpenCore changes what the firmware measures and can trigger a BitLocker recovery prompt.
- **Main path (D3 decided): erase.** The runbook leads with the erase path and puts keep-Windows in a clearly separated "alternative" box. For the erase path add:
  5. The recovery host is tested before the install: MacLoader installed on it, the identity export imported there, and `macloader autoload --readiness` runs. Record that a rebuild from that host needs a fresh ACPI capture and USB evidence only if the import path (P5.5, I8) cannot carry them; the copy of the built EFI (item 2) is the fast fallback for re-creating the stick.
  6. Also copy to the second medium: the P0.5 firmware before/after record and the sanitized session/evidence export, so nothing needed later lives only on the disk being erased.
  7. A final statement the owner reads before leaving Windows: "After the erase, this laptop has no operating system until the macOS install finishes. Windows returns only from the backup or a Windows installer."

### P10.3 Install procedure (docs) — REVIEW
- **Write** the step-by-step procedure that follows a passed Recovery smoke test: **wired Ethernet is mandatory** (plug the cable in before booting; Intel Wi-Fi does not work in Recovery and the installer downloads about 14 GB there); Disk Utility → View → Show All Devices; identify the internal disk by model and capacity (the existing fresh-confirmation rule at runbook line 76 stays); erase as APFS / GUID (whole disk or only the prepared partition per D3); Reinstall macOS; expect several reboots, each time choosing the "macOS Installer" entry in the OpenCore picker from the USB stick; first-run setup offline (skip Apple ID until later); record the installed build with `sw_vers`.
- **Post-install:** mount the internal EFI partition (`sudo diskutil mount diskXs1` after identifying it with `diskutil list`), then copy by option:
  - **Erase option:** the internal EFI partition is empty; copy the stick's whole `EFI` folder to it.
  - **Keep-Windows option:** the internal EFI partition already holds `EFI/Microsoft` and Windows' own `EFI/BOOT/BOOTx64.efi`. Never delete or replace `EFI/Microsoft`. First check free space (`df -h` on the mounted partition; the Windows partition is often only 100 MB) against the size of the stick's `EFI/OC`. If it fits: copy `EFI/OC`, rename the existing `EFI/BOOT/BOOTx64.efi` to `BOOTx64.efi.windows-backup`, then copy the stick's `EFI/BOOT/BOOTx64.efi`. If it does not fit: stop, keep booting from the stick, and record it as an open item; do not resize partitions from this procedure.

  Then eject the stick, boot with F12 choosing the internal disk, confirm the picker and macOS start. State that until this is done the laptop boots macOS only with the USB stick inserted. Wi-Fi after install needs the HeliPort app, downloaded over Ethernet.
- **Rollback:** how to boot Windows again (F12 → Windows Boot Manager) under "keep Windows"; how to restore from backup under "erase".

### P10.4 Pre-install gate in the guided flow — REVIEW — before Phase 8
- **Problem:** the flow ends at `Stage.COMPLETE` after seven smoke checkpoints (`macloader/autoloader/first_boot.py:39-63`); `installation_authorized` is hard-coded false (`macloader/autoloader/readiness.py:97`, `macloader/recovery/smoke.py:42-43`).
- **Design limit (D4):** MacLoader runs on the laptop being installed. Once the owner boots into Recovery, MacLoader is not running, and under "erase" its session no longer exists. So the guided flow can only gate what happens **before** leaving Windows. Everything from the erase onward is tracked on the printed checklist (P10.3) and recorded afterwards (P10.5). Do not add checkpoints for steps that happen in Recovery or macOS.
- **Change:** add `Stage.INSTALL` after the smoke checkpoints. It performs **no** disk action. It is three human-reported checkpoints, bound with the same `checkpoint_binding` as the first-boot ones, all completed in Windows: (1) every P10.2 safety item done, shown as a list including identity export, full stick copy on the rescue stick, recovery host rehearsal passed (P10.7) and the printed procedure; (2) D3 choice recorded (`erase` or `keep-windows`), stored in the artifact; when the choice is `erase`, a second typed phrase is required, `ERASE THE INTERNAL DISK AND WINDOWS`, and the message names the recovery host requirement; (3) Ethernet cable connected. Entering the stage requires the typed phrase `I ACCEPT INSTALLING AN UNVERIFIED MACOS BUILD ON THIS LAPTOP` (the `input_required` mechanism from P7.1). Leave `installation_authorized=False` in the smoke record (it describes the record, not the owner's decision); add a separate session artifact `install_acceptance` with the campaign digest, the D3 choice and a timestamp. After checkpoint 3 the flow reaches `Stage.COMPLETE` with the message "Pre-install checks recorded. Follow the printed install procedure. MacLoader cannot observe the installation."
- **Tests:** stage is unreachable without all seven smoke checkpoints; wrong phrase does not enter; checkpoints are strictly sequential; `install_acceptance` stores the D3 choice; a failed checkpoint (first-boot or install) offers "Stop and record" as well as "Repeat" (also fixes the non-terminal failure loop at `first_boot.py:42-48` [R]).

### P10.5 Acceptance record
- **Docs:** extend `docs/HARDWARE_ACCEPTANCE.md` with a blank results table matching runbook lines 90-99, to be filled after install, plus the post-erase steps MacLoader could not observe: erase done, installer finished, first desktop reached, installed build (`sw_vers`), EFI copied to the internal disk, boots without the stick. The profile stays `EXPERIMENTAL` until the owner reviews it.

### P10.6 First-boot troubleshooting (docs) — REVIEW
- **Write** a short runbook section used during the smoke boot and the install. The reviewer confirms each row against the Dortania troubleshooting pages before merge.
  - Where the log is: with `Misc.Debug.Target = 67` OpenCore writes `opencore-<date>.txt` to the root of the stick's EFI partition; read it from Windows after shutting down. Never paste it publicly without checking it for serial numbers.
  - Photograph the last verbose lines before any hang; the last line matters most.

  | Symptom | First thing to check |
  |---|---|
  | Stick missing from the F12 menu | Secure Boot and UEFI-only settings (P0.5); the stick is in a USB-A port |
  | Picker appears but no Recovery entry | `OpenHfsPlus.efi` present and listed (P4.1); `ScanPolicy` is 0 |
  | Stops at `[EB|#LOG:EXITBS:START]` | Booter quirks from P4.2; `AppleXcpmCfgLock` |
  | Verbose text, then a black screen | Add `-igfxblr` to boot-args as a recorded experiment; PNLF `_UID` (P3.4c) |
  | Prohibited sign, or "Still waiting for root device" | USB map and the port the stick is in (Phase 5) |
  | Keyboard or trackpad dead in Recovery | VoodooPS2 present and ordered (P4.3, P4.4); use a USB keyboard to continue |
  | Installer cannot download | Ethernet cable; IntelMausi present (P4.3) |

- Any boot-arg experiment is a config change: rebuild through MacLoader, do not hand-edit the stick's `config.plist`. The one allowed manual step is the whole-folder `EFI` copy described in P10.7, used only after the internal disk is erased.

### P10.7 Recovery host rehearsal: Linux live USB (docs, then owner) — REVIEW
- **Why:** D3 is "erase" and the recovery host is a Linux live USB booted on the T480s. After the erase it is the only place MacLoader can run. It must be proven to work **before** the erase, while Windows still exists as a fallback.
- **Hard limit [C]:** the guided writer is qualified only on Windows (`macloader/autoloader/media.py:37-40`, `75-76`), so on Linux the flow always stops at `WRITER_UNQUALIFIED` and MacLoader cannot write a stick there. Do not change that gate. Recovery therefore has two layers that do not need the writer, both run from the rescue stick:
  1. **Restore the full stick copy.** Everything on the installer stick is copied to the rescue stick (P10.2 item 2). If the installer stick's files are damaged, boot the rescue stick and copy them back; no MacLoader needed.
  2. **Linux rebuild for config changes only.** MacLoader on Linux rebuilds the EFI; the owner then replaces the `EFI` folder on the installer stick by hand (delete the stick's `EFI` folder, copy the newly built one, unmount cleanly). The Recovery folder is not touched.
- **Depends on:** P5.3 (canonical scope digests, so evidence is not stale under the Linux provider), P9.2 (identity export/import). Shares its procedure with P5.5; write one runbook section that serves both.
- **Worker confirms first and reports (no code change):**
  1. Where the built EFI tree lives in the workspace after the flow stops at `WRITER_UNQUALIFIED` on Linux, so the runbook can name the folder to copy from. If no complete `EFI` folder exists on disk at that point, stop and report; the orchestrator plans a follow-up.
  1b. That the written installer stick is a single FAT32 volume whose files can be copied off and back with an ordinary file manager (read `macloader/removable/windows_image.py` for the layout). If it has more than one partition or a non-FAT filesystem, stop and report: the "restore by file copy" layer then needs a different procedure.
  2. What the Linux path needs installed: Python 3.11+, `pkexec` (used by `macloader/evidence/acpi_capture.py:109-111`), and any disk tools the Linux adapter calls (list the executables it runs).
  3. Whether a configuration, ACPI evidence and USB evidence produced on Windows can be imported on Linux today, or must be captured again there.
- **Write** the runbook section "Recovery host":
  - Make the rescue stick as described in section 0.5, with a mainstream Linux distribution that ships Python 3.11 or newer. Give the fallback (two plain sticks) in one short box.
  - **No persistence.** The MacLoader workspace stays in the live session's home folder (in memory) and is lost on reboot. That is accepted: the Linux rebuild is the last-resort layer and repeats its own capture and downloads. Do not point `MACLOADER_WORKSPACE` at the rescue stick's folder area: that filesystem has no file permissions and the private-workspace checks may refuse it.
  - The `MacLoader-rescue` folder holds a copy of this repository at the qualified commit and the Python dependencies as downloaded wheels, so installing MacLoader in the live session does not depend on the network. Ethernet is still needed for the dependency and Recovery downloads.
  - The rescue stick is itself a USB disk and is mounted: say that MacLoader lists it as excluded and that it must never be selected or typed into an erase phrase.
  - The rehearsal, run once before the erase: boot the live USB on the T480s; install MacLoader from the second medium; `macloader identity import`; run `macloader autoload --readiness`; continue the guided flow until it stops at `WRITER_UNQUALIFIED`. The rehearsal passes when (a) the flow reached that stop with an EFI built, (b) the identity fingerprint equals the one on Windows, and (c) the owner has practised the manual step once: from the live session, replace the installer stick's `EFI` folder with the **saved copy** from the rescue stick (identical content, so the tested stick does not change), then confirm with F12 that the stick still reaches the OpenCore picker. The Linux-built EFI is not put on the stick during the rehearsal.
  - What to do after a failed install, in order of preference: retry with the installer stick as it is; restore the full stick copy from the rescue stick; only if the config must change, rebuild the EFI in the live session and replace the `EFI` folder by hand.
- **Owner step:** perform the rehearsal and record pass/fail outside the repo. The P10.4 gate lists "recovery host rehearsal passed" among the safety items.

---

## Phase 11 — Tests, CI and documentation

P11.1 and P11.2 are the only proof before hardware that the generated EFI and ACPI tables are valid. They run after Phase 5, P11.1 first and then P11.2 (which runs the P11.1 test with real tools), and both must be green before Phase 8. P11.3 and P11.4 can follow later.

### P11.1 End-to-end T480s EFI test (independent)
- **Problem:** no test builds a full EFI from the 20L8 fixture; `tests/integration/test_pipeline.py:8-14` uses 20L7; `tests/unit/test_autoloader_build.py:102-115` stubs the toolchain [R].
- **Change:** new `tests/integration/test_t480s_reference_efi.py`: start from `tests/fixtures/t480s/t480s_20l8_162_synthetic.json`, synthetic ACPI DSL fixtures, a synthetic USB evidence session, fake kext archives containing `Info.plist` and executables; build; assert on the resulting `config.plist`: every P4.2 key, driver order (P4.1), kext order (P4.4), ACPI entries exist on disk, `PlatformInfo.Generic.SystemProductName == "MacBookPro15,2"`, USB map kext present.

### P11.2 Real-tool validation job (independent)
- **Problem:** `tests/unit/test_p4_toolchain.py:25-45`, `130-154` falls back to a shell stub that echoes a version when no real tool exists, with `kexts=()` and `acpi_files=()` [R].
- **Change:** when the real toolchain is absent, `pytest.skip` with an explicit reason instead of substituting the stub in the test that claims to run real `ocvalidate`. Add a CI job (Ubuntu) that runs `macloader toolchain install`, then the P11.1 test with real `ocvalidate` and real `iasl` compiling the Phase 3 generated tables. In `.github/workflows/ci.yml`, install `gdisk`, `dosfstools`, `mtools` so `tests/unit/test_windows_native.py:556-558` stops skipping, and make that test fail (not skip) when `CI=true` and the tools are missing.

### P11.3 Windows CI strength (independent)
- **Change:** run the coverage gate on `windows-latest` too (`ci.yml:29-40`); add a Windows-only test that creates a file, applies the private-ACL helper (`macloader/workflow/service.py:435-453`) and asserts via `icacls` that inheritance is removed and only the current user has access; compare by SID, not localized names (`macloader/identity/service.py:88-93` [R]).

### P11.4 Documentation refresh (independent; after Phases 1-10)
- **Split:** `P11.4a` runbook rewrite (first bullet); `P11.4b` status documents and small fixes (second bullet); `P11.4c` `.gitignore` and the capture script (third bullet).
- Rewrite `docs/T480S_FIRST_INSTALL_RUNBOOK.md` around `macloader autoload` on a Windows host: elevated terminal, ACPI capture, USB port checks, identity phrase, writer qualification pointer, BIOS table, smoke boot, install procedure. Remove "Use Linux as the first software-preparation host" (line 13) and the 2026-10-02 Linux readiness paragraph (line 7).
- Update `docs/PROJECT_STATUS.md`, `docs/AUTOLOADER_V1_IMPLEMENTATION_STATUS.md`, `START_HERE.md`, `README.md` to match. Fix `%LOCALAPPDATA%\MacLoader\` → `Libre_Core-MacLoader` in `docs/WINDOWS_WRITER_QUALIFICATION.md`; fix "v0.1" in `docs/HARDWARE_ACCEPTANCE.md:47`.
- Add `t480s-followup-*/` and `PRIVATE-ACPI/` to `.gitignore`; change the default output of `tools/capture_t480s_followup.ps1:3` to `$env:LOCALAPPDATA\Libre_Core-MacLoader\captures`; make the script stop before the USB walk and say so when ACPI capture failed (lines 144-180) [R].

---

## Phase 12 — Improvements (after first install; each item independent)

All `[R]`; confirm before changing. One chunk each.

| Id | Area | Change |
|---|---|---|
| I1 | Guided start | Start screen listing what will be downloaded and read, with sizes, before any automatic work (`macloader/ui/guided.py:45-47`). |
| I2 | Blocked messages | Show failing field names and the required BIOS in `HARDWARE_MISMATCH`, `HARDWARE_UNKNOWN`, `CAMPAIGN_NOT_FOUND`, `CONFIGURATION_BLOCKED` (`service.py:82`, `150`, `160`, `186`). |
| I3 | BIOS string | Accept "N22ET85W 1.62" (space form) in `macloader/build/acpi.py:29-36`. |
| I4 | Engine BIOS awareness | `CompatibilityEngine.evaluate` returns `UNKNOWN` with a BIOS reason when no campaign matches (`engine.py:44-60`, `384-390`). |
| I5 | CLI snapshot freshness | `build`, `preflight`, `config check` re-probe and compare BIOS/machine binding (`cli.py:336`, `559`, `1095`). |
| I6 | Stale-config dead ends | Add a `config rebind` command for `POLICY_STALE` / `SNAPSHOT_STALE` (`macloader/configuration/service.py:62-67`). |
| I7 | USB evidence predicates | One shared predicate for "USB evidence complete" in preflight and evaluate (`configuration/service.py:264-276`, `workflow/service.py:300-304`). |
| I8 | Evidence import | Verify the private source at import; copy into the private store; absolutize `private_ref`; mark imported evidence as self-attested (`cli.py:424-435`, `macloader/evidence/usb.py:120-123`). |
| I9 | Workspace path | Probe writability by creating a file; warn on the `%TEMP%` fallback; absolutize `MACLOADER_WORKSPACE`; print the workspace in `toolchain status` (`macloader/config.py:14-29`). |
| I10 | Windows file contention | Retry-with-backoff around `os.replace`/unlink and lock acquire; map to a "busy, retry" error (`macloader/dependencies/cache.py:253-304`, `downloader.py:127`, `configuration/store.py:85-91`). |
| I11 | Archive errors | Wrap `BadZipFile`/`OSError` in `ArchiveSecurityError`; reject `<>"|?*` in names (`macloader/dependencies/archive.py:57-65`, `161-178`). |
| I12 | Tool provenance | Record upstream checksums for `iasl.exe` and `acpidump.exe` (`toolchains/catalog.yaml:70-73`, `86-89`). |
| I13 | Engineering TUI | Explicit "Probe live hardware" action; guard preflight worker; confirm before shortcuts cancel running work (`macloader/ui/tui.py:141-207`, `442-455`). |
| I14 | Readiness | Check the private identity; locate the repo from the package path; parse rename lines; make `--readiness` read-only (`macloader/autoloader/readiness.py:27-61`, `cli.py:68`). |
| I15 | Deferred requirements | Reviewed profile declares deferred requirements by ID instead of string matching (`configuration/service.py:113-178`). |
| I16 | Kext updates | Review the pending WhateverGreen 1.7.1 / VirtualSMC 1.3.8 / AppleALC 1.9.8 candidates through the existing maintenance flow. |
| I17 | Audio fallback | Match audio by PCI ID when no codec record exists, or block explicitly (`engine.py:168-170`, `456-467`). |
| I18 | Packaging | Raise the setuptools floor for recursive package-data; assert the approvals YAML is in the wheel (`pyproject.toml:2`, `33-34`). |
| I21 | WWAN field | Add `wwan: List[NetworkInfo]` to `HardwareSnapshot`, written by `to_dict()` only when non-empty so existing digests stay identical; read it in `from_dict()`; add it to MAC redaction in `macloader/detection/sanitize.py:63`; the engine adds the warning "WWAN is not supported and is left unmapped" and no component result. Do not add a WWAN fact to `hardware_facts` or to the engine's inventory list. Also store the USB identity on Windows ethernet/wifi entries, which the `net` branch drops today. |
| I22 | PM981 from Windows | Found in P1.4 [C by worker test]: a PM981 detected on Windows (model `MZVLB...`, no PCI id on the disk row) matches `standard-nvme`, so `nvme_compatibility_fix` never fires; `_match_storage` lowercases the model but not the YAML patterns, so `*PM981*` patterns cannot match either (`macloader/compatibility/engine.py` about line 273, `storage.yaml`). Fix the case handling and add an `MZVLB` rule. Not needed for the reference SSSTC drive. Also: a USB disk attached through UASP reports `InterfaceType` SCSI and a `SCSI\DISK` id, so the P1.4 USB exclusion does not catch it; exclude by bus type from `Get-Disk`/`MSFT_Disk` instead. |
| I23 | Linux removable disks | `macloader/detection/linux.py` lists every whole `sd*` disk, USB sticks included, as `StorageInfo(kind="sata")`, and `StorageInfo` has no removable/bus field. Read `/sys/block/<dev>/removable` or the USB transport and exclude or tag those disks. Matters once a Linux writer exists. Also: the Linux provider puts `usb_count` into `raw_evidence` (`linux.py:678`), which is still part of `stable_snapshot_digest`, so on Linux a stick still invalidates saved artifacts on resume (P2.2b fixed Windows only). |
| I24 | Test hygiene | `tests/unit/test_guided.py::test_late_selection_message_after_unmount_is_harmless` builds a `GuidedApp` with an empty service, so `on_mount` runs a live `probe_hardware()` during the suite (found in P2.4). Inject a fixture snapshot instead; add a guard that fails any test reaching the real provider. Also: `advance_until_blocked`'s `Stage.DETECT` fallback and the guided `session is None` path call `start()` with no arguments and reset the stored private material (test seam only). |
| I19 | Release config | A second reviewed `config_overrides` set for daily use after acceptance: no `-v`, `Misc.Debug.Target` 3, `HideAuxiliary` true, a picker timeout. Selected explicitly; the first-install set stays the default. |
| I20 | Wi-Fi after install | Document HeliPort for itlwm on Sequoia (downloaded over Ethernet) in the post-install section; decide whether Wi-Fi and Bluetooth kexts should be optional for the first install to reduce risk. |

---

## Path to the first install, in order

**Where we are (2026-11-17):** P0.1, P0.6, Phase 1 and Phase 2 are done; P2.7 is next. Phase 1 and 2 did not need owner facts, so they ran ahead of the owner's Phase 0 steps. From Phase 3 on, chunks start to depend on them: P3.1 can run now, but P3.2 needs the SSDT count from P0.3, P3.3/P3.4 need the DSDT names from P0.3, and P4.2 needs the `Sample.plist` table from P0.2. The owner steps (P0.2, then P0.5a by the orchestrator, then P0.5, P0.3, P0.4) should therefore happen before Phase 3 goes past P3.1.

1. Phase 0 in the order P0.1, P0.6, P0.2, P0.5a (orchestrator confirms the firmware table), P0.5, P0.3, P0.4. Firmware settings are final from here on.
2. Phases 1-4 (detects correctly, builds an EFI that can boot) → Phase 5 (USB map from Windows; P5.3 and P5.6 always run) → P11.1 (end-to-end EFI test) → P11.2 (the same test with real `ocvalidate` and `iasl`) → Phase 6 (Recovery downloads).
3. Phase 7 (safe writer; `P7.6d` last). From here the five backend files and `autoloader/media.py` are frozen. Phase 7 comes first because P9.1 and P10.4 reuse the typed-input mechanism that P7.1 adds.
4. Phase 9, then P10.1 and P10.4 (the last chunks that change campaign data or stages; none of them may touch the frozen files). From here all policy data is frozen too.
5. Phase 8: owner qualifies the writer on a sacrificial stick; approval merged.
6. P10.2, P10.3, P10.5, P10.6, P10.7 written and reviewed; the install section printed.
7. Owner runs `macloader autoload` on the T480s to a written USB, then does the picker/Recovery smoke boot (seven checkpoints), recorded back in Windows. The owner then copies the installer stick's contents and the identity export to the rescue stick and completes the Linux rehearsal (P10.7).
8. Owner completes the pre-install gate (P10.4) in Windows, then installs from the printed procedure with Ethernet connected, and fills in P10.5 afterwards.
9. P11.3, P11.4 and Phase 12 follow the install.
