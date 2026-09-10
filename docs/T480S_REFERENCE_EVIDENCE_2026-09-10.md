# T480s reference evidence — 2026-09-10

## Scope and privacy

This record summarizes private physical-machine evidence for the first Sequoia candidate. Raw captures and ACPI tables remain under ignored `workspace/private-t480s-evidence/` and must not be committed or published. A BIOS photograph disclosed serial, UUID, MAC and OEM licence information in the private conversation; none of those values is reproduced here or required by policy.

Target: macOS Sequoia 15.0 build 24A335 candidate. This is evidence for implementation, not a successful macOS installation or a `SUPPORTED` promotion.

## Sanitized machine baseline

- Lenovo ThinkPad T480s, machine family 20L8
- Intel Core i5-8250U, 4 cores / 8 logical processors
- 24 GiB memory
- Intel UHD Graphics 620; internal non-touch 1920×1080 panel at 60.03 Hz
- SSSTC CA5-8D256-HP 256 GB NVMe storage
- Intel Wireless-AC 8265 and Intel Bluetooth
- Intel I219-V Ethernet
- Fibocom L830-EB WWAN/GNSS device
- Realtek ALC257 (`10EC:0257`), Lenovo subsystem `17AA:2258`
- ELAN precision touchpad, PS/2 keyboard and working TrackPoint
- No physical touchscreen, fingerprint reader, smart-card reader or keyboard backlight
- Camera, microphone, speakers, keyboard, touchpad, TrackPoint, Wi-Fi, Bluetooth, HDMI, audio, LAN, SD reader and external USB connectors reported operational under Windows

## Firmware baseline

- Lenovo UEFI `N22ET85W` 1.62, dated 2025-09-05; EC 1.23
- UEFI-only boot; CSM disabled; Secure Boot disabled
- Intel virtualization and VT-d enabled; Hyper-Threading enabled
- USB UEFI support and Always On USB enabled
- Thunderbolt BIOS Assist disabled; Wake by Thunderbolt enabled
- Thunderbolt security set to User Authorization; pre-boot Thunderbolt device support disabled
- Boot display is ThinkPad LCD; shared-display priority USB Type-C; total graphics memory 256 MB

These are observed settings, not final installation instructions. Any later BIOS changes invalidate the firmware-bound evidence and require review.

## ACPI and USB evidence

Official ACPICA 20260408 `acpidump.exe` was accepted only at SHA-256 `a0095a57521378c290d030db7ad196a27de2fcc770dd0347104ff49d19d792e0`. The real run produced one DSDT and eleven SSDTs. Independent inspection confirmed that every file's declared ACPI length matches its file length and every ACPI checksum is zero. The tool returned `-1` after emitting the SSDT set, so the collector's original JSON says `failed`; the files themselves passed the independent structural checks. This does not replace later `iasl` disassembly/compilation qualification.

Observed internal XHCI routes:

- `HS06`: Fibocom L830-EB WWAN
- `HS07`: Intel Bluetooth
- `HS08`: integrated camera
- `SS03`: Realtek USB 3.0 card reader
- `SS01`: left USB-A beside HDMI, tested with USB mass storage
- `SS02`: right USB-A beside the cooling vent, tested with USB mass storage

Both USB-C connectors were user-confirmed working in both orientations. Windows retained both SanDisk device nodes during the snapshots, so the capture did not uniquely correlate the Type-C sockets with logical XHCI routes. Preserve that as unresolved evidence and validate Type-C data during controlled boot/installed-macOS acceptance; use a positively identified USB-A route for the first installer.

Private integrity manifests:

- Follow-up ACPI bundle manifest SHA-256: `26c875ca84f761e11aeee564e70d2e092ee25a24b12c39722174656163a0b515`
- Final USB/audio bundle manifest SHA-256: `589506914ee4ee08040e0c2ffb08caad48cfb5abcece0b84d58ce960300fed7e`

## P4 consequences

- Bind all derived ACPI/configuration output to this exact BIOS and evidence set.
- Disassemble and inspect the private DSDT/SSDT set with a pinned `iasl`; do not copy patches from an unrelated EFI.
- Resolve ALC257 layout selection from reviewed policy, initially among the bounded candidates already recorded by the configuration workflow; physical output/input testing remains required.
- Treat USB-A `SS01`/`SS02` and internal routes as observed. Do not claim a complete final USB map from the unresolved Type-C correlation.
- Keep WWAN unsupported/disabled unless an explicit reviewed policy is added.
- Do not generate or expose SMBIOS identity values in tracked files, logs, manifests or conversation.
