# Support Matrix

> This file describes **MacLoader-verified support**, not everything that may be possible in the wider Hackintosh community.

## Status Meanings

- `SUPPORTED` — Passed defined MacLoader physical hardware acceptance suite.
- `CONDITIONAL` — Usable with a documented limitation or required configuration/workaround.
- `EXPERIMENTAL` — Implemented and researched but not yet fully physically accepted.
- `BLOCKED` — Known incompatible or deliberately unsupported by MacLoader.
- `UNKNOWN` — Insufficient evidence to determine compatibility.

---

## Supported Laptop Models

| Model | Machine Types | Sonoma (14.x) | Sequoia (15.x) | Tahoe (16.x) | Physical Acceptance |
|---|---|---|---|---|---|
| **Lenovo ThinkPad T480s** | `20L7`, `20L8` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | Pending (Fixture verified) |
| **Lenovo ThinkPad T480** | `20L5`, `20L6` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | Pending (Fixture verified) |
| **ThinkPad X1 Carbon 6th** | `20KH`, `20KG` | `BLOCKED` | `BLOCKED` | `BLOCKED` | Deliberately out of scope for v0.1 |
| **Non-Lenovo PCs** | *Any* | `BLOCKED` | `BLOCKED` | `BLOCKED` | Unsupported |

---

## Component Matrix

| Category | Component / Device | Identifier(s) | Sonoma | Sequoia | Tahoe | Notes |
|---|---|---|---|---|---|---|
| **CPU** | Intel Core 8th Gen (KBL-R) | i5-8250U, i5-8350U, i7-8550U, i7-8650U | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | Native 4C/8T support |
| **iGPU** | Intel UHD Graphics 620 | `8086:5917`, `8086:3ea0` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | Requires WhateverGreen |
| **dGPU** | Nvidia GeForce MX150 | `10de:1d10`, `10de:1d12` | `CONDITIONAL` | `CONDITIONAL` | `CONDITIONAL` | Must be disabled via SSDT / `-wegnoegpu` |
| **Audio** | Realtek ALC257 | `10ec:0257`, `8086:9d71` | `EXPERIMENTAL` | `EXPERIMENTAL` | `CONDITIONAL` | AppleHDA removed in Tahoe |
| **Ethernet** | Intel I219-LM / I219-V | `8086:15d7`, `8086:15d8` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | Supported via IntelMausi |
| **Wi-Fi** | Intel AC 8265 / 9560 / AX200 | `8086:24fd`, `8086:2526`, `8086:2723` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | OpenIntelWireless; No native AirDrop |
| **Bluetooth** | Intel Bluetooth Controller | `8087:0a2b`, `8087:0aaa` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | IntelBluetoothFirmware + BlueToolFixup |
| **Storage** | Samsung PM981 / PM981a | `144d:a808`, `144d:a809` | `CONDITIONAL` | `CONDITIONAL` | `CONDITIONAL` | Requires NVMeFix; power stability warning |
| **Storage** | Standard NVMe / SATA SSD | PCI Class `0108` / `0106` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | Standard native storage |
| **Touchscreen** | ELAN / Synaptics Touchscreen | `04f3:*`, I2C Bus | `EXPERIMENTAL` | `EXPERIMENTAL` | `CONDITIONAL` | VoodooI2C + VoodooI2CHID |
| **Trackpad/TrackPoint** | ThinkPad UltraNav | SynPS/2, TPPS/2 | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | VoodooPS2Controller / VoodooRMI |
| **Thunderbolt** | Intel JHL6240 (Alpine Ridge) | `8086:15bf`, `8086:15d3` | `EXPERIMENTAL` | `EXPERIMENTAL` | `EXPERIMENTAL` | Thunderbolt 3 Controller |

> Note: Do not promote any component to `SUPPORTED` until the physical acceptance checklist is executed on physical hardware.
