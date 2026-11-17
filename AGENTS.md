# Working in this repository

MacLoader prepares OpenCore boot media for one experimental target: ThinkPad T480s 20L8, BIOS N22ET85W 1.62, macOS Sequoia 15.0. The development host is Windows 11 running on that T480s.

## Where things are

- **Picking up the work:** [docs/HANDOVER.md](docs/HANDOVER.md) says what is done, what to do next, and how to hand work back for review.
- **Active plan:** [docs/T480S_FIRST_INSTALL_FIX_PLAN.md](docs/T480S_FIRST_INSTALL_FIX_PLAN.md). Its "Progress" table says what is done and which chunk is next; section 0.1 holds the worker rules.
- **Current status and open gates:** [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).
- **Design records (not plans):** `docs/AUTOLOADER_V1_MASTER_PLAN.md`, `docs/ENGINEERING_SPEC_V0.1.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`.

## Commands

`python` is not on PATH on this host. From the repo root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rf
.\.venv\Scripts\python.exe -m mypy macloader
git diff --check
```

The full suite takes about 2 minutes. Check the pytest exit status directly; piping through `tail` hides failures. Write multi-line commit messages to a file and use `git commit -F <file>` (PowerShell 5.1 mangles `-m` strings with quotes).

## Never run without the owner present

`macloader usb ...`, `macloader identity generate`, `macloader autoload` (any mode), `macloader recovery download`, `macloader toolchain install`, `python -m macloader.removable.windows_qualification --run`, `macloader probe` against the live machine, and any `diskpart`, `Format-*`, `Clear-Disk` or `Set-Disk`. Never `git push`, rebase or reset: the owner pushes after review.

## Rules that are easy to break

- No serial numbers, UUIDs, MAC addresses, ACPI tables, SMBIOS values or user-profile paths in code, tests, fixtures, docs or commit messages. Fixtures use invented values.
- `macloader/removable/windows_native.py`, `windows_image.py`, `windows_qualification.py`, `adapters.py`, `writer.py` (and, after fix-plan P7.6d, `macloader/autoloader/media.py`) feed the writer approval digest. Change them only in fix-plan Phase 7, never after Phase 8 starts.
- YAML under `macloader/database/data/` with `policy_version`, `revision` or `*_policy_id` fields needs the version bumps the plan describes; run `tests/unit/test_reference_campaigns.py` and `tests/unit/test_database_loader.py` after any data edit.
- Changes to campaign, profile or evidence policy data after the owner's real `autoload` run force a rebuild and a repeat smoke boot, so that data is frozen once Phase 8 starts.
- Do not widen a chunk. If the code does not match the plan, or the fix needs a file outside the chunk's list, stop that chunk and record what you found in the plan.

## Workflow used on this project

One chunk of the fix plan at a time, each on its own `plan/<chunk-id>` branch, one commit per chunk, full suite and mypy green before fast-forwarding local `main`. Each chunk gets a "Result" bullet in the plan and the "Progress" table is kept current. Work is reviewed commit by commit before the owner pushes.
