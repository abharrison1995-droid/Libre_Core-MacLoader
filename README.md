# Libre_Core MacLoader

MacLoader prepares and reviews OpenCore configurations for ThinkPad hardware. The first supervised installation target is the ThinkPad T480s 20L8 with BIOS N22ET85W 1.62 and macOS Sequoia 15.0 build 24A335.

## Readiness

The CLI, shared workflow service, Textual TUI, pinned dependency acquisition, EFI generation/validation code, a read-only Linux ACPI capture command, and the Linux USB preparation implementation are present. Host tools can be provisioned into a clean workspace and checked against the bundled catalog. The real EFI builder passes the matching pinned `ocvalidate` on synthetic, software-only inputs. The Linux writer pins every operation to the locked device's kernel identity and verifies both GPT copies, the ESP geometry and type, FAT32 metadata, and every payload file on readback. It has passed only simulated and disposable-image tests. No physical USB writer is qualified, and physical writes remain disabled.

The exact Apple Recovery payload for build 24A335 is an external blocker. No Apple-authoritative, authenticated route binds a Recovery product or payload to that build: Apple's `AP` response field is only a product identifier, and the pinned discovery protocol has no build metadata or selector. See the [Recovery decision record](docs/RECOVERY_BUILD_BINDING_DECISION.md). `preflight` derives the Recovery gate from the discovery evidence recorded by `macloader recovery resolve`. No payload or product-to-build guess is accepted. Do not substitute another macOS target.

This checkout has no private machine ACPI capture, selected real SMBIOS identity, or qualified physical USB. Structural checks, synthetic tests, a passing `ocvalidate`, and disposable media do not establish a successful physical installation. See the [first-install runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) for checkpoints and current gates.

## Install and inspect

```bash
python -m pip install -e ".[dev]"
macloader --help
macloader toolchain status
macloader preflight --fixture tests/fixtures/t480s/t480s_20l8_bios162_synthetic.json --json
```

That fixture is a **synthetic, software-only** 20L8 / N22ET85W 1.62 snapshot for trying the workflow. It is not evidence from any machine: it is marked synthetic, preflight reports `reference_machine` as blocked for it, and ACPI import and EFI builds refuse it. The other checked-in T480s fixtures describe a 20L7 with BIOS 1.53 and are used only for regression tests.

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

# On that same laptop, capture its firmware DSDT/SSDTs read-only (Linux, root),
# then import the validated capture into private storage.
sudo "$(command -v macloader)" evidence acpi-capture ~/t480s-acpi-private
macloader evidence acpi-import CONFIG_ID ~/t480s-acpi-private

# Acquire/check catalog-pinned host tools and dependencies.
macloader toolchain install
macloader preflight --config CONFIG_ID --json

# Build/validate EFI only after machine evidence, review, and identity checkpoints.
macloader build --config CONFIG_ID --output /path/outside/the/repository/EFI
macloader validate /path/outside/the/repository/EFI
```

Replace `CONFIG_ID` and paths with the actual reviewed inputs. Do not use any checked-in fixture, synthetic or 20L7, as evidence for the real 20L8 machine. `config check`, `plan`, `build`, and preflight resume from the private snapshot saved by `config new` when `--fixture` is omitted.

The shared preflight reports the missing prerequisites and their next actions:

```bash
macloader preflight --config CONFIG_ID --json
macloader tui --config CONFIG_ID
```

The TUI can acquire verified dependencies from an empty cache. It does not bypass missing machine-bound evidence or enable an unqualified physical writer.

## Recovery and media gates

`macloader recovery resolve` does not accept an unbound product ID as proof of build 24A335. It records the redacted outcome, including transport failures, as current private evidence. `macloader recovery status` and `preflight` report the gate derived from that evidence and never report it ready. Signed chunklist validation and bounded HTTPS acquisition remain enforced. Continue only when Apple provides an authenticated exact-build relationship; the [decision record](docs/RECOVERY_BUILD_BINDING_DECISION.md) lists what would unblock it.

Linux is the first supported preparation host for qualification. The Linux device discovery and guarded layout/write/readback code is implemented. After locking, every command targets the locked disk's attachment-unique `/dev/disk/by-diskseq` path, and alias retargeting or hotplug replacement stops the operation. Payload files larger than FAT32's 4 GiB − 1 byte limit are rejected while planning. Physical writing remains disabled until a sacrificial USB campaign is separately approved and passes. Use `macloader usb list` and non-destructive planning for inspection only. Never use an internal/system disk. Windows readback failures invalidate media before remount.

## Verification

```bash
python -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79
python -m mypy macloader tests --follow-imports=skip
python -m compileall -q macloader tests
git diff --check
# Optional, needs GitHub access, make, GCC, Bison, Flex and m4: provision the pinned
# toolchain into a clean workspace and validate a synthetic EFI with the real ocvalidate.
MACLOADER_NETWORK_TESTS=1 python -m pytest -q tests/integration/test_toolchain_efi_integration.py
```

These gates establish software behavior only. Physical USB boot, Recovery, installation, rollback, and hardware acceptance require the separate checkpoints in the [T480s runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md).
