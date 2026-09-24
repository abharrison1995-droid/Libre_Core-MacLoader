# Start here

MacLoader is preparing for a first supervised install on a ThinkPad T480s 20L8 with BIOS N22ET85W 1.62, targeting Sequoia 15.0 build 24A335. Read the current readiness note in [README](README.md) and the [first-install runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) before using the workflow.

## Current stop gates

- No Apple-authoritative, authenticated route binds a Recovery product or payload to build 24A335 (see the [decision record](docs/RECOVERY_BUILD_BINDING_DECISION.md)). `macloader recovery resolve` records current discovery evidence, and preflight derives the gate from it; the gate is never ready.
- There is no private ACPI capture or selected real SMBIOS identity in the current workspace. Capture ACPI on the T480s with `sudo "$(command -v macloader)" evidence acpi-capture DIRECTORY` (see the runbook).
- Linux is the first preparation host. Disposable-image tests pass, but no physical USB writer is qualified. Physical writes remain disabled.
- No BIOS settings, internal disks, or physical USB media have been changed.

Do not substitute a different macOS version/build. The first eventual physical boot must reach the OpenCore picker and Recovery without changing the internal disk. Installing to a disk requires a separate confirmation that names the exact target disk.

## Set up the software

```bash
python -m pip install -e ".[dev]"
macloader toolchain install
```

Tool bytes are checked against the catalog and installed in the configured per-user workspace. Set `MACLOADER_WORKSPACE` before invoking MacLoader when using a different workspace. Do not store private data or generated artifacts in the repository.

## Start a local workflow

On the actual reference machine, create and review a draft, then inspect its complete blocker list:

```bash
macloader config new
macloader config set CONFIG_ID --version 15.0 --build 24A335
macloader config check CONFIG_ID
macloader preflight --config CONFIG_ID --json
```

Capture the raw ACPI tables read-only on the 20L8 / BIOS 1.62 machine itself, then import them:

```bash
sudo "$(command -v macloader)" evidence acpi-capture ~/t480s-acpi-private
macloader evidence acpi-import CONFIG_ID ~/t480s-acpi-private
```

The capture refuses any other machine or BIOS. The import verifies the DSDT and SSDT set and keeps the raw tables in the owner-only private workspace. Do not paste ACPI tables or SMBIOS values into tickets, exports, manifests, logs, or chat. Generate or reuse a real SMBIOS identity only at its deliberate workflow checkpoint; that action is not part of software smoke testing.

For an interactive workflow:

```bash
macloader tui --config CONFIG_ID
```

The TUI can download and verify missing catalog dependencies. Missing machine-bound evidence and external Recovery/media gates remain visible blockers.

## Check software and physical gates

```bash
python -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79
python -m mypy macloader tests --follow-imports=skip
python -m compileall -q macloader tests
git diff --check
```

Read [docs/T480S_FIRST_INSTALL_RUNBOOK.md](docs/T480S_FIRST_INSTALL_RUNBOOK.md) for the BIOS baseline, USB-A SS01/F12 path, Recovery networking, failure capture, disk selection, rollback and hardware acceptance steps. A passing test suite, a generated synthetic EFI, or a real `ocvalidate` result is software evidence only; none proves physical boot or installation success.
