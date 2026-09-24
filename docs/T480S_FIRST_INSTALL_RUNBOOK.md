# ThinkPad T480s first-install runbook

## Scope and current readiness

This runbook is for the reference ThinkPad T480s machine type **20L8**, BIOS **N22ET85W 1.62**, with the frozen target **macOS Sequoia 15.0, build 24A335**. Do not change the target without a separate user decision.

The source tree currently has no private machine ACPI capture, selected real SMBIOS identity, exact verified Recovery payload, or physically qualified USB writer. Apple Recovery discovery returned HTTP 405 over HTTPS; the Apple `AP` field supplies a product identifier and does not prove a macOS build. Software and disposable-image results cannot close those gates. **No physical media campaign or first boot is ready to start yet.** The next Recovery artifact needed is an Apple-authoritative authenticated binding to build 24A335 or an Apple-signed Recovery payload independently bound to that build.

The earlier [reference evidence note](T480S_REFERENCE_EVIDENCE_2026-09-10.md) is historical. Its raw ACPI files and other private captures are absent from this checkout, so its measurements and firmware observations are not current evidence for this installation.

## Before a preparation session

Use Linux as the first preparation host. Install MacLoader and acquire the catalog-pinned tools:

```bash
python -m pip install -e ".[dev]"
macloader toolchain install
macloader toolchain status --json
```

Toolchain files and dependencies belong in the per-user workspace (or a separate path selected with `MACLOADER_WORKSPACE`). Keep raw evidence, private identities, downloaded Recovery payloads, generated EFI trees, and media images outside the checkout and out of Git.

Create a configuration from the actual reference laptop and freeze the exact target:

```bash
macloader config new
macloader config set CONFIG_ID --version 15.0 --build 24A335
macloader config check CONFIG_ID
macloader preflight --config CONFIG_ID --json
```

Capture the DSDT and all eleven SSDTs on that same laptop/BIOS, transfer the raw directory through a private channel, and import it:

```bash
macloader evidence acpi-import CONFIG_ID PRIVATE_ACPI_DIRECTORY
macloader preflight --config CONFIG_ID --json
```

The importer checks the complete table set, AML headers, declared lengths, and checksums, then stores raw data under owner-only private permissions. Do not publish ACPI contents, SMBIOS values, serials, UUIDs, or private paths in logs, exports, manifests, screenshots, or conversation. Generate a real SMBIOS identity only when the supervised workflow reaches that checkpoint; the exact command requires the deliberate confirmation phrase shown by the CLI. The identity stays in protected local storage.

Do not continue to USB preparation while `exact_recovery`, `private_acpi`, `private_identity`, or `physical_media` is missing, blocked, or unqualified. Linux disposable block-image coverage is software evidence only. A sacrificial physical USB qualification still needs its own fresh checkpoint and isolated failure campaign.

## Record the BIOS baseline

Before boot testing, enter ThinkPad Setup only to read and record the current values. Confirm the machine type is **20L8** and the installed UEFI BIOS is **N22ET85W 1.62**; Lenovo lists that BIOS version for T480s types 20L7/20L8 in its [official BIOS history](https://support.lenovo.com/lu/en/downloads/DS502226). The [T480s User Guide](https://download.lenovo.com/pccbbs/mobiles_pdf/t480s_ug_en.pdf) describes the BIOS menus and the boot menu.

Record, without changing, the displayed UEFI/CSM or Legacy setting, Secure Boot state, storage mode, graphics settings, virtualization/VT-d state, Thunderbolt settings, boot order, and any security settings relevant to external boot. Photograph the relevant screens privately and redact serial numbers, UUIDs, asset tags, ownership data, and network addresses before sharing any diagnostic. No exact settings capture is present in the current workspace. If the firmware version differs, or any setting needs changing, stop: obtain a separate BIOS-change checkpoint, preserve rollback notes, and recapture machine-bound ACPI evidence after the approved change.

## First physical boot: picker and Recovery only

After the Recovery and physical-media blockers are closed and the user has approved the exact sacrificial USB device, perform the first boot as a non-installation test:

1. Confirm the media label and stable whole-device identity against the approved write record. Disconnect unrelated external storage. Do not select an internal disk in the writer.
2. Insert the USB stick into the **left USB-A port beside HDMI**, historically identified as SS01. Confirm the actual port on this laptop rather than relying solely on the old note.
3. Power on and tap **F12** at the Lenovo logo. Choose the UEFI USB entry from the one-time boot menu; this avoids changing persistent boot order. Lenovo's guide documents F12 device selection.
4. Confirm that OpenCore reaches its picker. Photograph or privately record the screen and selected entry. Do not choose an installer action yet.
5. Start the prepared Recovery entry and confirm that it reaches the Recovery environment. **Do not open Erase, Partition, or Install actions.** The first acceptance goal ends at the Recovery utilities screen.
6. Shut down or reboot after the test, remove the stick when it is safe, and confirm the existing internal OS still starts.

The objective of this pass is the OpenCore picker and Recovery with the internal drive untouched. A picker alone does not prove Recovery boot; reaching Recovery does not prove installation or hardware acceptance.

## Recovery networking

Apple requires a broadband connection using DHCP over Wi-Fi or Ethernet to install macOS from Recovery ([Apple Support](https://support.apple.com/en-ie/102655)). Prefer a known-good wired Ethernet path if it is available through a verified adapter/dock. Otherwise use a compatible Wi-Fi network with the correct password. Avoid captive portals, enterprise sign-in pages, VPN-only access, and networks requiring browser enrollment.

At the Recovery screen, connect through its Wi-Fi menu or verify the wired link and DHCP status. If Apple server reachability needs a test, use only a read-only connectivity check approved for that session and stop before any disk action. Record network type, whether DHCP succeeded, Apple server reachability, displayed error text, and elapsed time. Do not enter credentials into untrusted prompts or share Wi-Fi secrets in failure notes. If Recovery asks to download an OS and the exact build cannot be confirmed, cancel and return to the picker; do not accept an offered substitute.

## Failure capture

Keep one private incident folder per attempt, outside the repository. Record UTC/local timestamp and timezone, BIOS version, USB stable device reference (redact serial in shared copies), selected F12 entry, OpenCore entry, last visible stage, network type, and the exact non-secret error text. Take photos of the display rather than transcribing guessed causes. Include OpenCore debug logs only in private storage; inspect and redact before sharing because logs may contain machine identifiers and device details.

Do not retry after a write/readback failure until the writer has invalidated the medium and the failure has been captured. If invalidation or safe eject reports an error, do not unplug or reuse the device until its state has been checked from the preparation host. Preserve the failed stick for review; do not point a retry at a newly enumerated disk without checking the complete stable identity again.

## Installation target: separate approval required

This step is outside the first-boot checkpoint. Installing macOS can erase the selected target. Before any erase, partition, or install action, request a **fresh confirmation that identifies the exact disk** using all available values: device identifier as displayed in Recovery, model, capacity, and serial or another unique hardware identity. Confirm that it is the internal installation disk, distinguish it from the USB stick and any other attached storage, state the consequence, and have the user approve that exact target. If Recovery does not show enough information to identify one unique disk, stop and collect more evidence. Never infer the target from disk number, position, or an earlier boot.

Only after the separately approved target is identified may the supervised installation proceed. Do not erase or partition any disk as part of the picker/Recovery smoke test.

## Rollback

For a failed picker/Recovery test, return to the OpenCore picker or power off, remove the USB when safe, then start the internal OS through the normal F12 choice if it does not boot automatically. No persistent BIOS or internal-disk change should have been made during this first pass. If firmware settings were changed under a separate approval, restore only the recorded prior values and verify the internal OS; do not improvise if a value is uncertain.

For an installation failure after the later approval, do not erase again or run a repair command against an uncertain disk. Capture the error, boot the still-preserved existing system where possible, and review the exact selected target and installer logs before any retry.

## Hardware acceptance after installation

Mark each item with evidence, not expectation. A working OpenCore picker or Recovery is not an acceptance result.

| Area | Acceptance evidence |
|---|---|
| Boot and storage | Cold boot, restart, shutdown, internal NVMe detection, and stable return to the installed system |
| Graphics and display | UHD 620 acceleration evidence, native panel resolution/brightness, sleep/wake display recovery |
| Input | Internal keyboard, TrackPoint, touchpad gestures/buttons, external USB keyboard/mouse |
| Audio | ALC257 output, speakers/headphone jack, microphone input, mute/volume controls |
| Network | Ethernet, Wi-Fi association and traffic, Bluetooth pairing/audio or input device |
| Ports | Both USB-A ports, SD reader, HDMI output, and each USB-C port in both orientations with data and charging tested separately |
| Other hardware | Camera, microphone privacy behavior, battery reporting/charging, sleep/wake, and thermal/fan behavior |
| Recovery and rollback | Recovery entry remains available and the documented rollback path reaches the intended OS |

For every item record pass/fail/not tested, exact steps, logs or photos stored privately, and any limitation. Keep untested hardware marked untested. Do not promote the T480s profile to supported until the full acceptance evidence and exact Recovery/media gates are reviewed.
