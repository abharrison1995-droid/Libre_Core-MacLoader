# Libre_Core MacLoader

MacLoader is a Python CLI and Textual application for inspecting ThinkPad hardware, reviewing OpenCore configuration policy, and preparing EFI output. Its guided workflow currently has one reviewed candidate: ThinkPad T480s 20L8, BIOS N22ET85W 1.62, macOS Sequoia 15.0 build 24A335. That candidate is **experimental**; physical installation and hardware acceptance have not been completed.

For the guided implementation and current qualification gaps, see [Autoloader implementation status](docs/AUTOLOADER_V1_IMPLEMENTATION_STATUS.md). [Project status](docs/PROJECT_STATUS.md) retains the earlier host audit. The [first-install runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) describes the physical checkpoints. No model is currently `SUPPORTED` by MacLoader physical acceptance.

## Install and inspect

```bash
python -m pip install -e ".[dev]"
macloader --help
macloader toolchain status
macloader preflight --json
```

The workspace defaults to a per-user cache directory. Set `MACLOADER_WORKSPACE` before launching MacLoader to select another location. Keep raw hardware evidence, private identities, cached assets, generated EFI, and media images outside the Git checkout.

## Guided Autoloader

Launch on the actual reference laptop:

```bash
macloader
# Text prompts use the same resumable service:
macloader autoload --terminal
# Read-only next-step and physical gate reports:
macloader autoload --status
macloader autoload --readiness
```

MacLoader selects the matching campaign and owns private session paths, tool and
dependency preparation, firmware collection, USB observations, identity binding,
EFI generation and validation. It stops for physical port movements, deliberate
prototype/identity/Recovery choices and qualified media decisions. Software never
interprets missing touch evidence as non-touch. Unsupported hardware and unknown
required facts remain blocked.

The normal path needs no configuration IDs, evidence paths, logical port IDs or
identity filenames. `macloader tui` and the explicit `config`, `evidence`,
`toolchain`, `build`, `recovery` and `usb` commands remain in Engineering/Advanced.
Do not use a fixture as evidence for a physical machine.

## Readiness boundary

Apple's current Recovery discovery flow does not prove that a returned product belongs to build 24A335. Exact Recovery remains externally blocked. An explicitly selected smoke-only mode verifies Apple-signed payload bytes while keeping the actual build unproven and installation unauthorized. Removable-media listing and planning are non-destructive; no physical USB writer is qualified and writes remain disabled. Software tests, generated EFI, `ocvalidate`, or disposable-image checks do not establish successful boot or installation.

Installing macOS or changing a disk requires separate approval naming the exact target. Read the [runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) before any physical campaign.

## Developer checks

```bash
python -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79
python -m mypy macloader tests --follow-imports=skip
python -m compileall -q macloader tests
git diff --check
```

These checks establish software behavior only. See the [hardware acceptance checklist](docs/HARDWARE_ACCEPTANCE.md) for the separate physical qualification scope.
