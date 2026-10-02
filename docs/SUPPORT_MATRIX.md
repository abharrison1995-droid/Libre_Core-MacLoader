# Support and qualification matrix

This matrix distinguishes implemented software policy from physical acceptance. A YAML record, fixture, generated EFI, successful structural check, or `ocvalidate` result does not establish hardware support. See [Project status](PROJECT_STATUS.md) for the latest observed blockers.

## Status meanings

- `SUPPORTED` — the defined physical acceptance suite passed for the exact machine, BIOS, OS build, and configuration.
- `EXPERIMENTAL` — a reviewed software profile or compatibility path exists, but physical acceptance is incomplete.
- `CONDITIONAL` — a reviewed limitation or workaround applies; it does not itself mean physical acceptance.
- `BLOCKED` — MacLoader deliberately prevents progression for the relevant evidence/policy state.
- `UNKNOWN` — current evidence is insufficient.

## Model and target scope

| Scope | Software available | Current qualification |
|---|---|---|
| ThinkPad T480s 20L8, BIOS N22ET85W 1.62, Sequoia 15.0 build 24A335 | Reviewed experimental profile, configuration workflow, EFI build/validation path | `EXPERIMENTAL`; exact Recovery is externally blocked; physical media and machine acceptance are pending. |
| Other T480s machine types or BIOS versions | Model and compatibility policy records | Not inherited from the 20L8 candidate; no physical acceptance claim. |
| ThinkPad T480 20L5/20L6 | Compatibility policy and software fixtures | No reviewed T480 EFI candidate in the current guided profile set; not physically accepted. |
| Sonoma or Tahoe | Compatibility/dependency policy notes remain in the repository | No exact selectable release in the current guided release catalog; no current EFI or physical qualification claim. Tahoe is macOS 26. |
| Other PCs | Detection may identify hardware, but policy is conservative | No supported installation profile. |

## Current release and device gates

| Area | Current state |
|---|---|
| Exact target | Sequoia 15.0 / build 24A335 is frozen for the first candidate. No substitute target is accepted implicitly. |
| Toolchain | Catalog-pinned OpenCore 1.0.7, `ocvalidate`, iASL, and macserial can be verified and acquired. Current observed local toolchain state is recorded in [Project status](PROJECT_STATUS.md). |
| EFI | Profile-driven generation and validation are implemented. A software-valid EFI is not evidence of physical boot. |
| Recovery | Discovery does not authenticate the product-to-build relationship for 24A335; acquisition must remain blocked without exact authenticated evidence. |
| USB media | Device inspection and non-destructive planning are implemented. Production physical writing remains disabled until host/backend qualification and a sacrificial-device campaign pass. |
| Physical acceptance | No picker, Recovery, install, installed-boot, or complete hardware acceptance record exists for this candidate. |

No row is currently `SUPPORTED`. Update this matrix only from evidence recorded for the exact tested scope; do not generalize acceptance across BIOS versions, machine types, OS builds, or optional hardware.
