# Libre_Core MacLoader

MacLoader prepares and reviews OpenCore configurations for ThinkPad hardware. The first supervised installation target is the ThinkPad T480s 20L8 with BIOS N22ET85W 1.62 and macOS Sequoia 15.0 build 24A335.

## Readiness

The CLI, shared workflow service, Textual TUI, pinned dependency acquisition, EFI generation/validation code, and Linux USB preparation implementation are present. Host tools can be acquired into the configured workspace and checked against the bundled catalog. The current Linux writer has passed disposable-image tests; no physical USB writer is qualified.

The exact Apple Recovery payload for build 24A335 is an external blocker. Apple's HTTPS discovery endpoint currently returns HTTP 405 in the reviewed flow. Apple's `AP` response field is a Recovery product identifier and does not establish the macOS build. No payload or product-to-build guess is accepted. Do not substitute another macOS target.

This checkout has no private machine ACPI capture, selected real SMBIOS identity, or qualified physical USB. Structural checks, synthetic tests, a passing `ocvalidate`, and disposable media do not establish a successful physical installation. See the [first-install runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) for checkpoints and current gates.

## Install and inspect

```bash
python -m pip install -e ".[dev]"
macloader --help
macloader toolchain status
macloader preflight --fixture tests/fixtures/t480s/t480s_baseline.json --json
```

The default workspace is a per-user cache directory. Set `MACLOADER_WORKSPACE` before starting MacLoader to use another workspace. Configuration snapshots, raw ACPI evidence, identities, cached dependencies, tools, Recovery files, and generated EFI are stored outside the Git checkout. Keep private data and generated output out of commits.

## Reviewed workflow

Use the reference machine itself for a supervised installation configuration. Fixtures support deterministic software checks only.

```bash
# Start a local draft; the real machine snapshot is kept in the private workspace.
macloader config new

# Select the frozen target and inspect issues.
macloader config set CONFIG_ID --version 15.0 --build 24A335
macloader config show CONFIG_ID
macloader config check CONFIG_ID

# Import that machine's validated DSDT plus eleven SSDTs into private storage.
macloader evidence acpi-import CONFIG_ID PRIVATE_CAPTURE_DIRECTORY

# Acquire/check catalog-pinned host tools and dependencies.
macloader toolchain install
macloader preflight --config CONFIG_ID --json

# Build/validate EFI only after machine evidence, review, and identity checkpoints.
macloader build --config CONFIG_ID --output /path/outside/the/repository/EFI
macloader validate /path/outside/the/repository/EFI
```

Replace `CONFIG_ID` and paths with the actual reviewed inputs. Do not use the checked-in 20L7 fixture as evidence for a 20L8 machine. `config check`, `plan`, `build`, and preflight resume from the private snapshot saved by `config new` when `--fixture` is omitted.

The shared preflight reports the missing prerequisites and their next actions:

```bash
macloader preflight --config CONFIG_ID --json
macloader tui --config CONFIG_ID
```

The TUI can acquire verified dependencies from an empty cache. It does not bypass missing machine-bound evidence or enable an unqualified physical writer.

## Recovery and media gates

`macloader recovery resolve` does not accept an unbound product ID as proof of build 24A335. Signed chunklist validation and bounded HTTPS acquisition remain enforced. Continue only when Apple provides an authenticated exact-build relationship or an Apple-signed payload independently bound to 24A335.

Linux is the first supported preparation host for qualification. The Linux device discovery and guarded layout/write/readback code is implemented, but physical writing remains disabled until a sacrificial USB campaign is separately approved and passes. Use `macloader usb list` and non-destructive planning for inspection only. Never use an internal/system disk. Windows readback failures invalidate media before remount.

## Verification

```bash
python -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79
python -m mypy macloader tests --follow-imports=skip
python -m compileall -q macloader tests
git diff --check
```

These gates establish software behavior only. Physical USB boot, Recovery, installation, rollback, and hardware acceptance require the separate checkpoints in the [T480s runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md).
