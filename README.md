# Libre_Core MacLoader

MacLoader is a Python CLI and Textual application for inspecting ThinkPad hardware, reviewing OpenCore configuration policy, and preparing EFI output. Its guided workflow currently has one reviewed candidate: ThinkPad T480s 20L8, BIOS N22ET85W 1.62, macOS Sequoia 15.0 build 24A335. That candidate is **experimental**; physical installation and hardware acceptance have not been completed.

For the reviewed software capabilities, current blockers, and evidence limits, see [Project status](docs/PROJECT_STATUS.md). The [first-install runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) describes the physical checkpoints. No model is currently `SUPPORTED` by MacLoader physical acceptance.

## Install and inspect

```bash
python -m pip install -e ".[dev]"
macloader --help
macloader toolchain status
macloader preflight --json
```

The workspace defaults to a per-user cache directory. Set `MACLOADER_WORKSPACE` before launching MacLoader to select another location. Keep raw hardware evidence, private identities, cached assets, generated EFI, and media images outside the Git checkout.

## Guided configuration

Create and review a configuration on the actual reference machine. Fixtures are for deterministic software checks, not physical evidence.

```bash
macloader config new
macloader config set CONFIG_ID --version 15.0 --build 24A335
macloader config check CONFIG_ID
macloader preflight --config CONFIG_ID --json
```

Import ACPI only from the bound 20L8 / BIOS 1.62 machine. After the prerequisites are ready, the guarded workflow can prepare and validate an EFI:

```bash
macloader evidence acpi-import CONFIG_ID PRIVATE_ACPI_DIRECTORY
macloader toolchain install
macloader build --config CONFIG_ID --output /path/outside/the/repository/EFI
macloader validate /path/outside/the/repository/EFI
```

Use `macloader tui --config CONFIG_ID` for the keyboard-accessible interface. CLI and TUI use the same workflow services. Do not use a checked-in fixture as evidence for a physical machine.

## Readiness boundary

Apple's current Recovery discovery flow does not prove that a returned product belongs to build 24A335. The preflight reports exact Recovery as externally blocked. Removable-media listing and planning are non-destructive; no physical USB writer is qualified and writes remain disabled. Software tests, generated EFI, `ocvalidate`, or disposable-image checks do not establish successful boot or installation.

Installing macOS or changing a disk requires separate approval naming the exact target. Read the [runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) before any physical campaign.

## Developer checks

```bash
python -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79
python -m mypy macloader tests --follow-imports=skip
python -m compileall -q macloader tests
git diff --check
```

These checks establish software behavior only. See the [hardware acceptance checklist](docs/HARDWARE_ACCEPTANCE.md) for the separate physical qualification scope.
