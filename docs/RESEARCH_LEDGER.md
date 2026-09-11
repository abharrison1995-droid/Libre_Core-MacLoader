# Research Ledger

Use one section per meaningful Hackintosh/platform decision.

---

## Research Item 001 — OpenCore Baseline Release

### Question
What is the authoritative, verified current upstream release of OpenCorePkg to use as the project baseline?

### Sources
- URL: https://github.com/acidanthera/OpenCorePkg/releases
- Project: OpenCorePkg (Acidanthera)
- Release/tag/commit: `1.0.7`
- Date inspected: 2026-08-29

### Findings
- Current stable release of OpenCorePkg is `1.0.7`.
- It includes upstream updates for modern macOS versions including Sequoia and early Tahoe compatibility hooks, memory mapping improvements, and `ocvalidate` validation binary updates.

### Decision
- Establish OpenCore `1.0.7` as the target baseline release policy for future resolver and validator stages.
- Keep OpenCore binary fetching out of the hardware detection and compatibility reporting tranches (v0.0.1 - v0.0.3) so the dependency resolver owns it cleanly in v0.0.4.

### Confidence
`HIGH`

### Physical verification required?
No

### Follow-up
- Validate against `Sample.plist` and `ocvalidate` in v0.0.4 and v0.0.7.

---

## Research Item 002 — Intel UHD 620 Framebuffer Policy (Kaby Lake Refresh)

### Question
How should Intel UHD Graphics 620 (`8086:5917`) on ThinkPad T480 and T480s be modeled in early compatibility reporting vs EFI generation?

### Sources
- URL: https://dortania.github.io/OpenCore-Install-Guide/config-laptop.plist/kaby-lake.html
- URL: https://github.com/acidanthera/WhateverGreen
- URL: https://github.com/MultimediaLucario/Lenovo-ThinkPad-T480
- URL: https://github.com/felikafelix/Hackintosh-Thinkpad-T480s
- Date inspected: 2026-08-29

### Findings
- Kaby Lake-R CPUs (i5-8250U, i5-8350U, i7-8550U, i7-8650U) contain Intel UHD Graphics 620 (PCI ID `8086:5917` or `8086:3ea0`).
- Upstream WhateverGreen supports this iGPU, but exact `AAPL,ig-platform-id` (e.g. `0x59170000`, `0x3EA50000`) and `device-id` spoofing vary across BIOS firmware versions, display configurations (FHD vs WQHD vs Touchscreen), and target macOS releases.
- Hardcoding specific framebuffer hex values prematurely into v0.0.3 creates rigid assumptions before EFI generation research.

### Decision
- v0.0.3 accurately detects Intel UHD 620 and retains its PCI IDs (`8086:5917`).
- v0.0.3 marks UHD 620 compatibility as `EXPERIMENTAL` and declares a required capability (`accelerated_intel_uhd_620`) in the preliminary BuildPlan.
- Premature hardcoding of specific `AAPL,ig-platform-id` values or connector patches is explicitly deferred to v0.0.5.

### Confidence
`HIGH`

### Physical verification required?
Yes (during v0.0.5/v0.1.0 on physical T480s).

### Follow-up
- Research optimal platform-id and connector patch sets for T480/T480s FHD and WQHD panels in v0.0.5.

---

## Research Item 003 — Realtek ALC257 Audio & macOS Tahoe AppleHDA Removal

### Question
How does audio support for Realtek ALC257 differ between macOS Sonoma/Sequoia and macOS Tahoe?

### Sources
- URL: https://github.com/acidanthera/AppleALC
- URL: https://github.com/wakhid-nusa/thinkpad-t480s-hackintosh
- Date inspected: 2026-08-29

### Findings
- Realtek ALC257 (`10ec:0257` / ALC3287) is supported by AppleALC under macOS Sonoma and Sequoia using layout IDs such as 11, 86, 97, or 99.
- In macOS Tahoe (macOS 26), Apple removed `AppleHDA.kext` entirely from the base OS in favor of newer audio infrastructure.
- As a result, standard AppleALC analogue audio injection alone is non-functional in Tahoe out of the box. Workarounds require VoodooHDA or AppleHDA reinjection/root patching.

### Decision
- Model ALC257 as `EXPERIMENTAL` for Sonoma and Sequoia.
- Model ALC257 as `CONDITIONAL` for macOS Tahoe with explicit warning regarding the removal of AppleHDA.
- Do not prematurely lock down layout ID 11 or 86 in v0.0.3; record the requirement for v0.0.5 EFI generator policy.

### Confidence
`HIGH`

### Physical verification required?
Yes

### Follow-up
- Investigate audio routing injection mechanisms for Tahoe in v0.0.5.

---

## Research Item 004 — Samsung PM981 / PM981a NVMe Compatibility & NVMeFix

### Question
Should Samsung PM981 NVMe drives be classified as unconditionally BLOCKED on modern macOS?

### Sources
- URL: https://github.com/acidanthera/NVMeFix
- URL: https://dortania.github.io/OpenCore-Install-Guide/
- Date inspected: 2026-08-29

### Findings
- The Samsung PM981 / PM981a OEM NVMe SSD (`144d:a808`, `144d:a809`) has known controller firmware quirks that trigger kernel panics (I/O timeout / APFS corruption) under macOS without workarounds.
- However, Acidanthera's `NVMeFix.kext` includes APST management workarounds that mitigate many PM981 timeout panics.
- Therefore, classifying PM981 as unconditionally `BLOCKED` would be inaccurate and would prevent generating a valid BuildPlan with NVMeFix remediation.

### Decision
- Classify Samsung PM981 as `CONDITIONAL` rather than `BLOCKED`.
- Require `NVMeFix.kext` in future EFI BuildPlans when PM981 is detected.
- Prominently emit warnings noting potential power management instability and advise drive replacement or thorough physical validation.

### Confidence
`HIGH`

### Physical verification required?
Yes

### Follow-up
- Test NVMeFix configuration against real PM981 hardware during storage validation.

---

## Research Item 005 — Intel Wireless LAN (8265/9560/AX-series) on macOS Tahoe

### Question
What is the compatibility posture for Intel Wi-Fi on macOS Sonoma, Sequoia, and Tahoe?

### Sources
- URL: https://github.com/OpenIntelWireless/itlwm
- URL: https://github.com/OpenIntelWireless/AirportItlwm
- Date inspected: 2026-08-29

### Findings
- Intel Dual Band Wireless-AC 8265 (`8086:24fd`) is supported via `AirportItlwm` or `itlwm` + `HeliPort` on macOS Sonoma and Sequoia.
- macOS Sonoma and newer dropped legacy `IO80211Family` plugins, requiring OS-specific AirportItlwm builds or standalone `itlwm`.
- macOS Tahoe introduces further network framework modernization, making native-style Wi-Fi integration highly experimental.
- Continuity, AirDrop, and Sidecar remain non-functional or severely limited on Intel wireless across all modern macOS releases.

### Decision
- Model Intel WLAN as `EXPERIMENTAL` across Sonoma, Sequoia, and Tahoe.
- Distinguish Tahoe with explicit warnings about legacy wireless framework shifts and lack of native Apple Continuity features.
- Avoid promising native-style Wi-Fi functionality on Tahoe during v0.0.3.

### Confidence
`HIGH`

### Physical verification required?
Yes

### Follow-up
- Verify OpenIntelWireless Tahoe builds in v0.0.4 / v0.0.5.

---

## Research Item 006 — Upstream Dependency Release Pinned Catalog (`2026.08-a`)

### Question
What official upstream release versions, tags, binary archive URLs, and cryptographic SHA-256 hashes constitute the verified dependency baseline for MacLoader release policy `2026.08-a`?

### Sources
- URL: https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.7
- URL: https://github.com/acidanthera/Lilu/releases/tag/1.7.2
- URL: https://github.com/acidanthera/WhateverGreen/releases/tag/1.7.0
- URL: https://github.com/acidanthera/VirtualSMC/releases/tag/1.3.7
- URL: https://github.com/acidanthera/AppleALC/releases/tag/1.9.7
- URL: https://github.com/acidanthera/IntelMausi/releases/tag/1.0.8
- URL: https://github.com/acidanthera/NVMeFix/releases/tag/1.1.3
- URL: https://github.com/acidanthera/VoodooPS2/releases/tag/2.3.7
- URL: https://github.com/VoodooI2C/VoodooI2C/releases/tag/v2.9.1
- URL: https://github.com/OpenIntelWireless/itlwm/releases/tag/v2.3.0
- URL: https://github.com/OpenIntelWireless/IntelBluetoothFirmware/releases/tag/v2.4.0
- URL: https://github.com/acidanthera/BrcmPatchRAM/releases/tag/2.7.2
- Date inspected: 2026-08-29

### Findings
- All assets were inspected and their binary payloads streamed directly from official GitHub releases to compute 64-character lowercase hexadecimal SHA-256 hashes:
  - `OpenCorePkg` `1.0.7`: RELEASE (`2ffab6ebf58c7aefb0bcb3a1a385d207746823d6dd87d44bd666e1286939943e`) / DEBUG (`3644db831dd18344896d7a86077b8c338c0eaa01b1579d7fa00785598cac1f2b`)
  - `Lilu` `1.7.2`: RELEASE (`53967d7dcfaab01023a33df2e969a89522f13d6654a6a56ac4711b62dabf3ab8`)
  - `WhateverGreen` `1.7.0`: RELEASE (`6d6ffe8334ad60f784a662794e67b2560b79d757d506841dc8ca9994ab39979b`)
  - `VirtualSMC` `1.3.7`: RELEASE (`12f1d379969f926306fa92d94ddbf33b32b31176589dc42089d864a26b31b700`)
  - `AppleALC` `1.9.7`: RELEASE (`81a8ba79986130e8c845fff595950226cbc30e588f8d37089e467f776469c29d`)
  - `IntelMausi` `1.0.8`: RELEASE (`cc02ea7e972ead536a51e5f6725e79f121026dbf36a766e97044ef2483fd29bc`)
  - `NVMeFix` `1.1.3`: RELEASE (`e1d5657ab7ac31f69771708f7b80bf218ab9aa0b8e4c4fe6ff943983037e3dfb`)
  - `VoodooPS2` `2.3.7`: RELEASE (`d5483298736ba4b3b82c7203d9a46968c668fb7af732b8b22c2096deb0acf83f`)
  - `VoodooI2C` `2.9.1`: RELEASE (`368e5eb60794583c340764813bd7e8244e94d3d3c06ea5b67034addd539487f4`)
  - `itlwm` `2.3.0`: RELEASE (`31ed2e5b4bca92645bfab0a397a1f50212c72a04ea15591c061fe74233e4f82b`)
  - `AirportItlwm` (Sonoma 14.4): RELEASE (`7ef12a8037eb9bc795828c348f4fa0728b8ade57aa0984b037816ef91c2d5119`)
  - `IntelBluetoothFirmware` `2.4.0`: RELEASE (`78418daa11f620012fc53ba68cbd643c2642a45f2f96c594fd01aaf2bc68fe2b`)
  - `BrcmPatchRAM` (for BlueToolFixup) `2.7.2`: RELEASE (`e1c1c55347526d031a8ae2fdd1f52efa3019161e497fb38e1cfa809752f8af21`)
- BlueToolFixup is distributed inside BrcmPatchRAM. The dependency catalog explicitly tracks this provenance without conflating it with IntelBluetoothFirmware.

### Decision
- Store all verified metadata in `macloader/database/data/dependencies/catalog.yaml` under policy `2026.08-a`.
- Require SHA-256 verification on all downloads and all cache reads. Checksum mismatch is a hard error.
- Never use unpinned or volatile "latest" redirect URLs for normal builds.

### Confidence
`HIGH`

### Physical verification required?
No

### Follow-up
- Re-verify hashes when evaluating future upstream release updates.

---

## Research Item 007 — Catalog release metadata audit

### Question
Do the pinned dependency tags, release assets, sizes and SHA-256 values in policy `2026.08-a` still match the official upstream releases?

### Sources
- Official GitHub release API endpoints for each repository and pinned tag, for example: https://api.github.com/repos/acidanthera/OpenCorePkg/releases/tags/1.0.7
- Official release asset URLs recorded in `macloader/database/data/dependencies/catalog.yaml`
- Date inspected: 2026-09-09

### Method
- Queried the official release API for every catalog entry and compared the pinned tag, asset name and byte size.
- Compared published GitHub asset digests where available.
- Streamed assets from the official `browser_download_url` and computed SHA-256 for releases whose API did not publish a digest.

### Findings
- Every pinned tag resolves and remains the latest release tag for its upstream repository as of the inspection date.
- All catalog RELEASE assets match the official asset name, size and digest. For repositories without a published API digest, the streamed hash matched the catalog value.
- Nine stale DEBUG records had incorrect size/hash pairs. The catalog now records the official asset metadata for Lilu, WhateverGreen, VirtualSMC, AppleALC, IntelMausi, NVMeFix, VoodooPS2, VoodooI2C and BlueToolFixup.
- The selected RELEASE archives contain every catalogued payload component. OpenCore stores the expected EFI tree under its official `X64/EFI/...` prefix; the builder maps that prefix to the published EFI destination.
- Upstream license files confirm BSD-3-Clause for the Acidanthera BSD projects, GPL-3.0 for IntelBluetoothFirmware, and Apple Public Source License 2.0 for VoodooPS2. The catalog now records the latter two corrections. NVMeFix's `LICENSE.txt` grants BSD-3-Clause terms even though GitHub reports `NOASSERTION` for the repository license field.
- The release archives do not consistently contain license text files. The repository now stores the license text fetched from each pinned upstream tag and the builder copies the exact notice into the generated `LICENSES` directory; package-data coverage and builder output tests protect this provenance.
- License files now have pinned SHA-256 values in the machine-readable catalog and generated manifests. The builder rejects missing, escaping, empty or modified notices before publication.
- Archive selection now prefers the official OpenCore `X64` member when both IA32 and X64 trees are present and rejects any remaining duplicate component matches. Artifact URLs must also name exactly the catalogued asset and may not carry query or fragment variants.
- No release tag was advanced solely because a newer development or unqualified artifact might exist. The pinned release set remains the qualification baseline until the G1 T480s/Sequoia policy and matching toolchain are frozen.

### Decision
- Keep the existing release versions and source URLs in policy `2026.08-a`.
- Correct the stale DEBUG size/hash records and set `date_verified` to `2026-09-09`.
- Correct the VoodooPS2 and IntelBluetoothFirmware SPDX identifiers in the catalog.
- Preserve the pinned license texts as package data and publish them with every generated EFI.
- Bind license notice digests and archive-member selection to the build evidence; do not qualify a build from a caller-supplied arbitrary duplicate or notice file.
- Treat archive member layout, license notices, host tool provenance and a real T480s/Sequoia build as separate G1 qualification work; this API audit does not close those gates.

### Confidence
`HIGH` for tag, asset, size and SHA-256 metadata; `PENDING` for archive layout, license-preservation and physical/toolchain qualification.

### Physical verification required?
No for release metadata; yes for the remaining G1 policy and toolchain decisions.

### Follow-up
- Inspect the selected RELEASE archive members and license files against the catalog destinations.
- Pin and record matching `Sample.plist`, `ocvalidate`, ACPI compiler, identity tooling and Recovery tooling on the supported host.

---

## Research Item 008 — G1 host-tool availability baseline

### Question
Are the host executables required for real G1/G2 qualification already available on the current Windows host?

### Sources
- PowerShell `Get-Command` lookup on the current build host
- Date inspected: 2026-09-09

### Findings
- Python 3.14 and Git are available.
- No `ocvalidate`, `iasl`/`iasl.exe`, or `macserial`/`macserial.exe` is installed or discoverable on `PATH`.
- The repository's Python validator stubs remain test fixtures only and do not qualify an OpenCore toolchain.

### Decision
- Keep G1 and G2 qualification open until matching, pinned host tools are acquired and their versions, architectures, sources and SHA-256 values are recorded.
- Do not promote a caller-supplied or test-only validator to `qualified` based on local availability.

### Confidence
`HIGH` for this host's current `PATH` state; `PENDING` for the toolchain acquisition and qualification.

### Physical verification required?
No for the availability check; yes for the resulting EFI and hardware policy.

### Follow-up
- Acquire the OpenCore 1.0.7 `ocvalidate` and matching `Sample.plist`, plus pinned ACPI and identity tooling, from their primary upstream sources.
- Capture the sanitized T480s/BIOS/OS snapshot and use it to select the actual policy inputs.

---

## Research Item 009 — OpenCore 1.0.7 Windows validator record

### Question
Does the pinned OpenCore 1.0.7 release contain the matching Windows validator and schema source needed to begin G2 qualification?

### Sources
- Official release asset: https://github.com/acidanthera/OpenCorePkg/releases/download/1.0.7/OpenCore-1.0.7-RELEASE.zip
- Official OpenCore 1.0.7 release record: https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.7
- Date inspected: 2026-09-09

### Verified record
- Archive: `OpenCore-1.0.7-RELEASE.zip`, 10,437,696 bytes, SHA-256 `2ffab6ebf58c7aefb0bcb3a1a385d207746823d6dd87d44bd666e1286939943e`.
- Schema source: `Docs/Sample.plist`, 55,244 bytes, SHA-256 `1bd3a503d2d71b531249e9d52fb1b8307e61547f6e593743da4969e5e2574203`.
- Windows validator: `Utilities/ocvalidate/ocvalidate.exe`, 615,895 bytes, SHA-256 `53e50246a9dc3006b938cea25c040a027b9ca9e0f700f7c8bdb0004f487b688f`.
- Validator documentation: `Utilities/ocvalidate/README.md`, 10,160 bytes, SHA-256 `9998ac0d8f4c07419dade27a4b1b53a86d973b645f1bee66e3f291261ea18229`.
- Executing the Windows validator without a config prints that it is compatible only with OpenCore 1.0.7 and returns its usage failure status, confirming the version banner but not a configuration pass.

### Decision
- Use this exact archive, Sample.plist member and Windows validator hash as the initial G1/G2 toolchain record.
- Keep tool qualification at `version-verified/config-unqualified` until the validator is run against a generated T480s config and the remaining ACPI, identity and Recovery tools are independently pinned.
- Do not commit the executable into the source tree; acquire it into a controlled local toolchain directory and bind its path and digest through the toolchain contract.

### Confidence
`HIGH` for the OpenCore archive/member provenance and version match; `PENDING` for full toolchain qualification.

### Physical verification required?
No for the version check; yes for the generated policy and final hardware acceptance.

### Follow-up
- Add a trusted toolchain record/loader that rejects caller-created `qualified` selections.
- Acquire and record ACPICA `iasl`, identity generation and Recovery tooling with matching host architecture and hashes.
- Run `ocvalidate.exe` against the first schema-based T480s config after the hardware policy is frozen.

---

## Research Item 010 — G1/G4 host toolchain and Recovery acquisition candidates

### Question
Can the public config schema, ACPI compiler, identity generator and Recovery client be obtained from pinned primary sources, and what evidence is still required before qualification?

### Sources
- [OpenCorePkg 1.0.7 release](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.7)
- [OpenCorePkg 1.0.7 release archive](https://github.com/acidanthera/OpenCorePkg/releases/download/1.0.7/OpenCore-1.0.7-RELEASE.zip)
- [OpenCore `macserial` source](https://github.com/acidanthera/OpenCorePkg/blob/1.0.7/Utilities/macserial/macserial.c)
- [OpenCore `macrecovery.py` source](https://github.com/acidanthera/OpenCorePkg/blob/1.0.7/Utilities/macrecovery/macrecovery.py)
- [OpenCore Recovery README](https://github.com/acidanthera/OpenCorePkg/blob/1.0.7/Utilities/macrecovery/README.md)
- [ACPICA 20260408 release](https://github.com/acpica/acpica/releases/tag/20260408)
- [ACPICA Windows binary download page](https://www.intel.com/content/www/us/en/developer/topic-technology/open/acpica/download.html)
- Date inspected: 2026-09-09

### Verified public records
- OpenCore 1.0.7 archive: 10,437,696 bytes, SHA-256 `2ffab6ebf58c7aefb0bcb3a1a385d207746823d6dd87d44bd666e1286939943e`.
- Schema/template: `Docs/Sample.plist`, 55,244 bytes, SHA-256 `1bd3a503d2d71b531249e9d52fb1b8307e61547f6e593743da4969e5e2574203`.
- Windows validator: `Utilities/ocvalidate/ocvalidate.exe`, 615,895 bytes, SHA-256 `53e50246a9dc3006b938cea25c040a027b9ca9e0f700f7c8bdb0004f487b688f`.
- Windows identity utility: `Utilities/macserial/macserial.exe`, 376,006 bytes, SHA-256 `e22df76f644d63302e8615216077366ee2f2880ca4aa7132c396bf2cf9da0e76`. Generation and verification are local; real serial, MLB, UUID and ROM values must stay in private storage.
- Recovery client: `Utilities/macrecovery/macrecovery.py`, 20,318 bytes, SHA-256 `68edc6c44b7f2b65bb23d5e5083cd1227a7f52f4bfaa716f7faa58d25be554b9`; its upstream workflow verifies Apple's signed chunklist and per-chunk image hashes, but `default`/`latest` is not an exact Sequoia build selector.
- ACPICA Windows package: `iasl-win-20260408.zip`, SHA-256 `121f5e4f30b1df63d09052294e4a605d4dee2dfb9599fa24af4ac6015df02b70`; required member `iasl.exe`, SHA-256 `739c597bcee4563f18d13e73b2051bb661707713e6154cf5712a1b7eef4a5df9`. The official Windows binary is PE32/IA-32 and runs under WoW64 on x86_64 Windows; it is not a native AMD64 executable.

### Decision
- Use the pinned OpenCore release as the source for the schema/template, matching validator, identity utility and Recovery client. Treat `Sample.plist` as a schema source only; it must never be shipped unchanged as a T480s configuration.
- Use ACPICA 20260408 as the current pinned iASL candidate, while recording its 32-bit Windows architecture as a host constraint.
- Add these records to the trusted toolchain loader and acquire executables into an ignored, controlled workspace directory. Do not commit public binaries to the repository.
- Use `macrecovery.py` as the Windows/Linux Recovery backend. Preserve the Apple response, resolved product/build, image/chunklist URLs and hashes, and verification result for every real acquisition. Label it as a third-party client of Apple infrastructure.
- Keep the toolchain state `version-verified/config-unqualified` until a generated T480s policy passes the real matching validator and the ACPI, Recovery and identity flows have been exercised on the supported host.

### Confidence
`HIGH` for the public release/member paths and locally computed hashes; `MEDIUM` for the absence of upstream-published detached checksums on the OpenCore and ACPICA binary assets; `PENDING` for actual T480s configuration, hardware and Recovery qualification.

### Physical verification required?
No for public artifact provenance or synthetic identity tests; yes for the T480s policy, Recovery boot path, and any real identity use.

### Follow-up
- Implement a trusted toolchain record/loader instead of accepting caller-supplied `qualification=qualified` metadata.
- Capture the sanitized live T480s/BIOS/OS snapshot and freeze the Sequoia policy, including ACPI paths and USB map.
- Generate a policy-based config from the pinned schema, validate it with the real matching `ocvalidate.exe`, and record the redacted report.
- Run an opt-in Recovery acquisition and retain its signed chunklist evidence. Exact build reproducibility requires recording the server-resolved product/build.

## Research Item 011 — Exact T480s graphics and audio policy inputs

### Question
Which authoritative public values are appropriate for the captured Kaby Lake Refresh UHD 620 and ALC257 candidate without inventing connector or physical audio results?

### Sources
- [WhateverGreen Intel graphics FAQ](https://github.com/acidanthera/WhateverGreen/blob/master/Manual/FAQ.IntelHD.en.md)
- [Dortania Kaby Lake laptop configuration guide](https://dortania.github.io/OpenCore-Install-Guide/config-laptop.plist/kaby-lake.html)
- [AppleALC supported codecs](https://github.com/acidanthera/AppleALC/wiki/Supported-codecs)
- [AppleALC changelog](https://github.com/acidanthera/AppleALC/blob/master/Changelog.md)
- Date inspected: 2026-09-10

### Findings and decision
- The reviewed graphics profile uses `AAPL,ig-platform-id=0000C087` and Kaby Lake Refresh fake `device-id=16590000` for the exact internal FHD non-touch policy. The profile is scoped to the captured panel and does not assert connector or brightness success.
- AppleALC publicly lists ALC257 layout candidates including 11, 18, 86, 96, 97, 99 and 100/101. The changelog identifies layout 86 with ThinkPad T480 and layout 97 with T490; layout 86 is therefore the bounded experimental selection for this T480s candidate.
- The selected values are encoded only in `macloader/database/data/profiles/t480s/p4-sequoia.yaml`, with source references and physical acceptance still required. No audio outcome or support promotion is inferred from the policy selection.

### Confidence
`MEDIUM-HIGH` for the public policy references and bounded values; `PENDING` for physical display/audio/connector acceptance on this exact machine.

### Physical verification required?
Yes — graphics acceleration, native resolution/brightness, speakers, headphone output, microphone and sleep/wake behavior remain untested.

## Research Item 012 — P4 trusted Linux toolchain and real EFI qualification

### Question
Can the exact captured T480s/Sequoia candidate be built from the locked dependencies using independently verified tools and a real matching OpenCore validator on the current host?

### Sources
- [OpenCorePkg 1.0.7 release](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.7)
- [OpenCore 1.0.7 release archive](https://github.com/acidanthera/OpenCorePkg/releases/download/1.0.7/OpenCore-1.0.7-RELEASE.zip)
- [ACPICA 20260408 release](https://github.com/acpica/acpica/releases/tag/20260408)
- OpenCore 1.0.7 `Docs/Sample.plist`, native Linux `ocvalidate` and native Linux `macserial` members from the pinned archive
- Date run: 2026-09-10

### Verified record and method
- The trusted catalog verifies the OpenCore archive/member digests, the schema digest, native Linux `ocvalidate` `1.0.7`, iASL `20260408` built from the official ACPICA source archive, and macserial `2.1.8`. Caller-supplied paths cannot declare a selection qualified.
- The private capture was validated machine-bound against the reviewed BIOS binding. All 13 tables disassembled; 12 OEM SSDTs compiled; the OEM DSDT compile failure was retained as a diagnostic and no OEM raw table was emitted. Three reviewed generated AML files compiled and were copied into the EFI ACPI directory.
- The exact configuration gate accepted Sequoia 15.0 build 24A335 after acknowledgements and evidence review. USB evidence remains `PARTIAL`; the profile records SS01 as the first-install route and keeps USB-C logical correlation unresolved.
- Ten pinned release dependencies were downloaded, integrity-checked and then replayed offline. The final schema-derived EFI passed structural validation and the real matching OpenCore 1.0.7 `ocvalidate` with exit 0; the redacted manifest binds output, toolchain, schema, profile, evidence, ACPI outputs, locks and identity reference.
- Automated result: `python3 -m pytest -q` — **275 passed in 9.88s**. Focused P4 tests cover forged toolchains, identity lifecycle, ACPI failure cases, kext ordering and real validator acceptance.

### Decision
P4 is software-qualified for this exact candidate on the current Linux x86_64 host, with explicit experimental and unresolved-evidence boundaries. Do not acquire Recovery, write installer media, modify firmware or promote the T480s to `SUPPORTED` from this result.

### Confidence
`HIGH` for the current host's tool bytes, version checks, locked dependency build and real validator result; `PENDING` for other hosts, Recovery, live identity choice and all physical acceptance.

### Follow-up
- Keep the raw ACPI capture, real/private identity values and ignored diagnostics outside Git and public artifacts.
- Complete Recovery, media, packaging/host-matrix and physical acceptance gates before any support-matrix promotion.

## Research Item 013 — P5 exact Apple Recovery discovery result

### Question
Does Apple's Recovery service identify an exact Sequoia 15.0 build `24A335` for the pinned OpenCore 1.0.7 Sequoia board request?

### Sources and method
- [OpenCorePkg 1.0.7 `macrecovery.py`](https://raw.githubusercontent.com/acidanthera/OpenCorePkg/1.0.7/Utilities/macrecovery/macrecovery.py)
- [OpenCorePkg 1.0.7 Recovery README](https://raw.githubusercontent.com/acidanthera/OpenCorePkg/1.0.7/Utilities/macrecovery/README.md)
- [Apple Recovery service](https://osrecovery.apple.com/)
- Date inspected: 2026-09-11
- The pinned OpenCore query was run once with board ID `Mac-7BA5B2D9E42DDD94` and the documented zero MLB. Session tokens and asset URLs were redacted and not retained in tracked files.

### Verified result
- Apple returned product identifier `696-28424` and image/chunklist metadata, but the response did not identify an exact macOS build or version.
- It therefore cannot prove the requested Sequoia `15.0/24A335` Recovery target. MacLoader classifies this as `AMBIGUOUS` and does not create a Recovery lock or download the payload.
- The pinned upstream client uses the legacy HTTP query endpoint. MacLoader's release policy requires HTTPS for authoritative discovery; the HTTPS endpoint returned HTTP 405 during a non-destructive probe. No weaker HTTP result is accepted as a release lock.
- No real Recovery image or chunklist was downloaded. No third-party mirror, alternate build, or target change was used.

### Decision and blocker
Keep the exact Sequoia `15.0/24A335` target frozen. P5 software contracts and failure handling are implemented, but the exact Recovery exit gate is **externally blocked** until Apple provides an exact authenticated relationship or the user explicitly chooses a different target. P7/P8 exact-target qualification cannot be claimed from this result.

### Confidence
`HIGH` that the recorded response was obtained from the pinned Apple service and did not identify `24A335`; `PENDING` for exact-target Recovery availability.

### Follow-up
- Do not silently substitute `696-28424` or any later Sequoia build.
- If the user approves a target change, record it as a new exact policy decision before any acquisition.

## Research Item 014 — P6 workflow and installed-package evidence

### Question
Does the shared CLI/TUI workflow preserve semantic configuration, issue, plan and lock results while remaining non-destructive and resumable outside the source checkout?

### Method and result
- Date run: 2026-09-11; local Linux x86_64, Python 3.13.
- `python3 -m pytest -q --cov=macloader --cov-branch --cov-fail-under=79`: **317 passed, 79.34% branch coverage**.
- `python3 -m mypy macloader tests --follow-imports=skip`: clean across 102 source files.
- `python3 -m compileall -q macloader tests` and `git diff --check`: clean.
- A wheel was built and installed into an isolated target outside the checkout. The artifact was `libre_core_macloader-0.0.4-py3-none-any.whl`, SHA-256 `f6d411eb1562d860851b93db51cd264334cc7892ead4c5a48e1abf6395ba9381`; the installed-package smoke compared CLI/TUI semantic results and passed. The host lacks the `build` module, so local sdist generation was not claimed; the tracked hosted CI workflow remains the source of wheel/sdist matrix evidence.
- Headless Textual tests exercise keyboard-accessible workflow actions, cancellation, safe resume, bounded import/export, evidence/review, Recovery cache verification and disabled media writes. Legacy standalone CLI utilities are documented as CLI-only utilities rather than implied one-screen-per-command TUI parity.

### Decision
P6 software behavior is locally qualified for the non-destructive schema-driven workflow. R6 completed with no unresolved correctness, security or release-blocking finding after repair. Hosted Windows/Linux CI, local sdist output and physical media remain open and are not represented as passed.

### Confidence
HIGH for local service/client parity and tested non-destructive boundaries; PENDING for hosted package/platform qualification and all physical gates.

### Follow-up
- Push the accepted tracked source and documentation so hosted CI can provide the required Windows/Linux wheel/sdist evidence.
- Keep P7 destructive media work behind its own sacrificial-device checkpoint and review gate.
