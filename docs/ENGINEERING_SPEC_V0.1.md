# Libre_Core MacLoader
## Engineering Specification — v0.1 Draft A

**Project:** Libre_Core-MacLoader  
**Package:** `macloader`  
**CLI:** `macloader`  
**Initial target hardware:** Lenovo ThinkPad T480 and T480s  
**Primary purpose:** Generate, validate and deploy machine-appropriate OpenCore/macOS installation environments for explicitly supported ThinkPad configurations.

---

# 1. Product Vision

Libre_Core MacLoader is a sibling project to Libre_Core AutoLoader.

Libre_Core AutoLoader automates ThinkPad firmware modification.

Libre_Core MacLoader automates the otherwise fragmented process of:

**detect hardware → determine compatibility → resolve OpenCore components → construct EFI → validate EFI → obtain macOS Recovery → build installation media → assist installation**

The project must reduce the amount of undocumented trial-and-error normally required to Hackintosh a supported laptop.

It is **not** intended to become a generic "Hackintosh any PC" utility during v0.1.

The first two hardware families are deliberately restricted to:

- Lenovo ThinkPad T480
- Lenovo ThinkPad T480s

---

# 2. Core Design Principle

MacLoader MUST NOT work by simply downloading or copying a prebuilt T480/T480s EFI.

The central architecture is:

**hardware snapshot + model profile + component profiles + macOS policy → BuildPlan → generated EFI**

Known community EFI repositories may be used as:

- research evidence;
- regression references;
- sources for understanding known quirks;
- hardware-behaviour documentation;
- test comparison data.

They must not become an opaque binary configuration that MacLoader blindly redistributes.

Reference projects are evidence, not authority.

---

# 3. v0.1 Product Promise

The eventual v0.1 release should be able to:

1. Identify a supported T480 or T480s.
2. Inventory relevant hardware.
3. Determine whether the detected configuration is supported for the chosen macOS release.
4. Explain unsupported or conditional components.
5. Resolve compatible OpenCore, kext, ACPI and driver components.
6. Generate a machine-appropriate `config.plist`.
7. Generate or compile required ACPI components.
8. Generate unique local SMBIOS identity information.
9. Construct a complete EFI directory.
10. Validate the EFI using the matching version of `ocvalidate`.
11. Produce a detailed build manifest.
12. Download legitimate macOS Recovery assets through Apple's recovery infrastructure/OpenCore tooling.
13. Build suitable USB installation media.
14. Provide a model-specific BIOS configuration checklist.
15. Guide the user through installation.
16. Preserve sufficient logs and metadata to support later automated boot diagnostics.

---

# 4. Explicit Non-Goals for v0.1

v0.1 does NOT include:

- arbitrary PC support;
- arbitrary ThinkPad support;
- P-series support;
- BIOS password bypass;
- firmware unlocking;
- BIOS flashing;
- Coreboot/Libreboot installation;
- automatic firmware modification;
- automatic recovery from every OpenCore boot failure;
- automatic post-install performance tuning;
- automatic OCLP patching unless explicitly added later as a separately researched feature;
- silent modification of an existing Windows/Linux bootloader;
- automatic writes to internal disks without explicit user action;
- redistribution of macOS installer images;
- redistribution of somebody else's entire prebuilt EFI.

Boot-log diagnostics are a v0.2 feature.

Post-install automation is a v0.3 feature.

---

# 5. Supported Hardware Policy

Support is evaluated at two levels.

## 5.1 Model support

Initially recognised models:

### ThinkPad T480
Known machine family: Lenovo ThinkPad T480

### ThinkPad T480s
Known machine family: Lenovo ThinkPad T480s

Machine-type identifiers and DMI aliases must be explicitly enumerated in model data rather than inferred from loose string matching.

An unknown machine MUST NOT silently receive the "closest" T480 profile.

---

# 6. Component Support Model

Hardware must be detected independently of the laptop model.

Important categories include:

- CPU
- iGPU
- dGPU
- chipset
- audio codec
- Ethernet controller
- Wi-Fi controller
- Bluetooth controller
- NVMe/SATA storage controllers
- internal display
- touchscreen
- trackpad
- TrackPoint
- USB controllers
- Thunderbolt controller
- webcam
- card reader
- WWAN hardware

A T480 is not considered equivalent to every other T480.

For example:

```text
T480
+ UHD 620
+ Intel Wi-Fi
+ no MX150
```

and:

```text
T480
+ UHD 620
+ Intel Wi-Fi
+ Nvidia MX150
```

are distinct component combinations and may produce different BuildPlans.

---

# 7. Compatibility States

Every important model/component/macOS combination receives one of:

`SUPPORTED`

Passed the defined physical hardware acceptance suite.

`CONDITIONAL`

Expected to work but has a documented limitation or required configuration.

`EXPERIMENTAL`

Implementation exists but has not passed the complete physical acceptance suite.

`BLOCKED`

Known incompatible or deliberately unsupported.

`UNKNOWN`

MacLoader does not possess sufficient evidence.

UNKNOWN must never be silently upgraded to SUPPORTED.

---

# 8. macOS Support Policy

Support must be version-aware.

The data model must be capable of expressing:

```text
T480s
 ├── Sonoma
 ├── Sequoia
 └── Tahoe

Intel 8265
 ├── Sonoma
 ├── Sequoia
 └── Tahoe

ELAN Touchscreen
 ├── Sequoia: supported
 └── Tahoe: conditional
```

MacLoader must never assume that a kext/configuration working on Sequoia necessarily behaves identically on Tahoe.

Initial development should research:

- macOS Sonoma
- macOS Sequoia
- macOS Tahoe

Tahoe is strategically important but must remain EXPERIMENTAL for a hardware configuration until physical testing passes.

---

# 9. OpenCore Baseline Policy

The dependency resolver must not permanently hardcode a single OpenCore version into Python source.

A release policy should specify:

- supported OpenCore release;
- download source;
- expected SHA-256;
- OpenCore configuration schema version;
- compatible dependency versions;
- date verified.

At project inception, the research baseline should be verified against the current upstream OpenCore release before implementation.

The design must permit later OpenCore releases without rewriting application architecture.

---

# 10. High-Level Architecture

```text
                       Libre_Core MacLoader

                            TUI / CLI
                               │
                               ▼
                         Orchestrator
                               │
            ┌──────────────────┼───────────────────┐
            │                  │                   │
            ▼                  ▼                   ▼
       Hardware Probe    Compatibility       Version Policy
            │               Engine                 │
            └──────────────────┬───────────────────┘
                               ▼
                           BuildPlan
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
     Dependency            ACPI Resolver       Config Builder
      Resolver                                    │
          │                    │                  │
          └────────────────────┼──────────────────┘
                               ▼
                           EFI Builder
                               │
                               ▼
                            Validator
                               │
                 ┌─────────────┴────────────┐
                 ▼                          ▼
            EFI Artifact              Build Manifest
                 │
                 ▼
            Recovery Builder
                 │
                 ▼
               USB Tool
```

---

# 11. Proposed Repository Structure

```text
Libre_Core-MacLoader/
│
├── macloader/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── orchestrator.py
│   ├── config.py
│   ├── exceptions.py
│   │
│   ├── domain/
│   │   ├── hardware.py
│   │   ├── compatibility.py
│   │   ├── build_plan.py
│   │   ├── artifacts.py
│   │   └── validation.py
│   │
│   ├── detection/
│   │   ├── base.py
│   │   ├── linux.py
│   │   ├── windows.py
│   │   ├── normalize.py
│   │   └── sanitize.py
│   │
│   ├── database/
│   │   ├── loader.py
│   │   ├── schema.py
│   │   └── data/
│   │       ├── models/
│   │       │   ├── t480.yaml
│   │       │   └── t480s.yaml
│   │       ├── components/
│   │       │   ├── graphics.yaml
│   │       │   ├── wifi.yaml
│   │       │   ├── bluetooth.yaml
│   │       │   ├── ethernet.yaml
│   │       │   ├── audio.yaml
│   │       │   ├── storage.yaml
│   │       │   └── input.yaml
│   │       └── macos/
│   │           ├── sonoma.yaml
│   │           ├── sequoia.yaml
│   │           └── tahoe.yaml
│   │
│   ├── compatibility/
│   │   ├── engine.py
│   │   ├── rules.py
│   │   └── report.py
│   │
│   ├── dependencies/
│   │   ├── resolver.py
│   │   ├── downloader.py
│   │   ├── checksums.py
│   │   ├── cache.py
│   │   └── manifest.py
│   │
│   ├── opencore/
│   │   ├── release.py
│   │   ├── builder.py
│   │   ├── config_builder.py
│   │   ├── identity.py
│   │   ├── drivers.py
│   │   ├── kexts.py
│   │   └── validator.py
│   │
│   ├── acpi/
│   │   ├── resolver.py
│   │   ├── compiler.py
│   │   └── sources/
│   │
│   ├── recovery/
│   │   ├── macrecovery.py
│   │   └── apple_catalog.py
│   │
│   ├── usb/
│   │   ├── devices.py
│   │   ├── safety.py
│   │   └── builder.py
│   │
│   ├── ui/
│   │
│   └── diagnostics/
│       └── logging.py
│
├── tests/
│   ├── fixtures/
│   │   ├── t480/
│   │   ├── t480s/
│   │   └── unsupported/
│   ├── unit/
│   ├── integration/
│   └── golden/
│
├── docs/
│   ├── ENGINEERING_SPEC_V0.1.md
│   ├── SUPPORT_MATRIX.md
│   ├── ARCHITECTURE.md
│   ├── RESEARCH_LEDGER.md
│   ├── HARDWARE_ACCEPTANCE.md
│   └── DECISIONS.md
│
├── workspace/
├── pyproject.toml
├── README.md
├── LICENSE
└── .gitignore
```

---

# 12. Domain Models

Application logic should operate on typed domain objects instead of passing arbitrary dictionaries everywhere.

At minimum:

## HardwareSnapshot

Contains normalized hardware information.

Example fields:

```text
manufacturer
product_name
machine_type
bios_version
cpu
igpu
dgpu[]
audio[]
ethernet[]
wifi[]
bluetooth[]
storage[]
usb[]
thunderbolt[]
input_devices[]
display[]
raw_evidence
```

Personally identifying fields such as serial number, UUID and MAC address should be sanitised before fixture/report persistence unless specifically required.

## SupportDecision

```text
target
state
reason
evidence
required_actions
known_limitations
```

## BuildPlan

The BuildPlan is one of the most important objects in the system.

It should describe everything MacLoader intends to put into the EFI before files are changed.

Conceptually:

```text
model = T480
macos = Tahoe

opencore = verified release

acpi:
  reviewed/generated ACPI set

kexts:
  resolved dependency set

drivers:
  resolved OpenCore drivers

graphics_policy:
  intel_uhd_620

identity_policy:
  version-specific SMBIOS policy

warnings:
  detected component limitations
```

The user must be able to inspect this plan.

---

# 13. Detection

Linux should be the richest initial probing environment.

Candidate sources include:

- `/sys`
- DMI
- `dmidecode`
- `lspci`
- `lsusb`
- ACPI information
- EFI variables where appropriate

Windows should have a provider using appropriate native facilities such as PowerShell/CIM/PnP information.

Detection must have a provider abstraction.

Tests MUST NOT require physical ThinkPads.

Recorded sanitized hardware fixtures should drive detection and compatibility tests.

CLI target:

```text
macloader probe
macloader probe --json
macloader probe --output hardware.json
```

---

# 14. Compatibility Engine

The compatibility engine consumes:

`HardwareSnapshot + target macOS + support database`

and returns:

`CompatibilityReport`

The report should answer:

- Is this machine recognised?
- Is the CPU supported?
- Is the iGPU supported?
- Is a dGPU present?
- Must the dGPU be disabled?
- Is Wi-Fi supported?
- Is Bluetooth supported?
- Is audio supported?
- Is Ethernet supported?
- Is storage known-good?
- Are there version-specific limitations?
- What BIOS settings are required?
- Can an EFI BuildPlan be safely produced?

A BLOCKED required component must prevent normal EFI generation unless an explicit developer override exists.

---

# 15. Hardware Database

Prefer declarative data over large Python `if model == ...` trees.

Example concept:

```yaml
id: thinkpad-t480s
vendor: LENOVO
product_names:
  - "<verified T480s identifiers>"

platform:
  family: kaby-lake-r

graphics:
  required:
    - intel-uhd-620

audio:
  known:
    - alc257

ethernet:
  known:
    - intel-i219-lm

optional:
  touchscreen: true
  thunderbolt: true
```

Exact identifiers must be researched before finalising.

Do not invent unsupported machine-type codes.

---

# 16. EFI Generation Philosophy

MacLoader builds an EFI from reviewed components.

It must not simply unpack:

`T480-EFI.zip`

or:

`T480s-EFI.zip`

Generation sequence:

```text
Resolve OpenCore release
        ↓
Create clean EFI skeleton
        ↓
Resolve drivers
        ↓
Resolve ACPI components
        ↓
Resolve kexts/plugins
        ↓
Generate config.plist
        ↓
Generate local identity
        ↓
Run structural validation
        ↓
Run ocvalidate
        ↓
Generate build manifest
```

---

# 17. config.plist Generation

Start from the `Sample.plist` belonging to the exact selected OpenCore release.

MacLoader should programmatically transform it according to:

- platform policy;
- model policy;
- detected hardware;
- macOS version;
- resolved ACPI;
- resolved kexts;
- resolved UEFI drivers;
- SMBIOS policy.

Do not maintain a giant hand-edited `config.plist` as the primary source of truth.

The final `config.plist` must be reproducible from BuildPlan + identity.

---

# 18. ACPI Strategy

v0.1 should favour reliability over cleverness.

Reviewed model/component-specific ACPI source may be stored as `.dsl`/ASL and compiled into `.aml`.

Preferred workflow:

```text
Reviewed ACPI source
        ↓
Variant resolver
        ↓
iasl compile
        ↓
AML artifact
        ↓
config.plist registration
```

Do not dynamically rewrite arbitrary DSDTs during v0.1 unless a demonstrated requirement exists.

Potential ACPI requirements must be researched against current OpenCore/Dortania guidance and actual T480/T480s ACPI.

The resolver must support conditional pieces.

Example:

```text
if Nvidia MX150 present:
    include reviewed dGPU-disable policy
else:
    do not include dGPU-disable component
```

---

# 19. Kext Resolver

Kexts are dependencies with:

```text
name
upstream
version
download artifact
sha256
minimum macOS
maximum macOS
dependencies
plugins
hardware applicability
```

The resolver must understand dependency ordering.

Do not blindly download "latest" on every run.

A verified compatibility set must be defined for a MacLoader release.

An explicit update workflow may later evaluate newer upstream releases.

---

# 20. Wi-Fi Strategy

Wi-Fi support must be component and macOS-version specific.

Initial priority should be common Intel Wi-Fi hardware found in T480/T480s systems.

Do not assume all machines contain the same WLAN card.

Intel and Broadcom policies must remain separate.

Where different Wi-Fi approaches have different OS-version constraints, the resolver must express that explicitly rather than treating them as interchangeable.

Unsupported WLAN should not necessarily block macOS installation if Ethernet is usable, but the limitation must be prominent.

---

# 21. T480 dGPU Handling

Some T480 configurations contain an Nvidia MX150.

MacLoader must detect its presence.

The BuildPlan must not treat an MX150 machine identically to an iGPU-only machine.

If the supported policy requires disabling the Nvidia GPU under macOS, that policy should be selected only when the hardware is actually present.

---

# 22. USB Mapping

Do not rely indefinitely on bootstrap quirks as the production solution.

The architecture must support proper model/variant USB maps.

If temporary bootstrap behaviour is required during development, it must be clearly distinguished from the final supported configuration.

---

# 23. SMBIOS / Identity

Unique identifiers MUST be generated locally.

MacLoader must never:

- ship a shared serial number;
- contain a developer's personal MLB;
- contain a committed SystemUUID;
- reuse identity values from a reference EFI.

Identity generation should use the tooling supplied/derived from the selected OpenCore release where practical.

Identity output includes as required:

- SystemSerialNumber
- MLB
- SystemUUID
- ROM policy

Identity generation should occur late enough that deterministic EFI-generation tests can run without generating real identities.

Tests should use clearly fake fixture identity data.

---

# 24. SMBIOS Policy

SMBIOS selection must be a version-controlled policy.

It must NOT be hardcoded solely as:

```text
T480 = X
T480s = Y
```

The selected Mac model may depend upon:

- physical hardware;
- target macOS release;
- graphics requirements;
- current Apple/OpenCore behaviour.

The reason for every SMBIOS policy must be documented in `RESEARCH_LEDGER.md`.

---

# 25. Dependency Integrity and Provenance

Every downloaded executable/archive should record:

- project;
- upstream URL;
- release/tag;
- SHA-256;
- licence;
- retrieval date;
- local cache path.

A generated EFI should produce:

`build-manifest.json`

containing sufficient information to reconstruct the build.

Do not put sensitive identity values into the public manifest.

---

# 26. Validation Pipeline

A build is NOT valid merely because files were copied successfully.

Required validation stages:

### Structural validation
Verify expected EFI layout.

### Dependency validation
Verify required parent/plugin relationships and files.

### Configuration validation
Verify config entries correspond to actual files.

### Duplicate validation
Detect duplicate ACPI, kext or driver entries.

### Version validation
Verify dependencies satisfy target macOS policy.

### Identity validation
Ensure required identity fields exist for release builds without exposing them.

### ocvalidate
Run the `ocvalidate` binary from the same OpenCore release that generated the EFI.

A failing `ocvalidate` result prevents the build from being marked valid.

---

# 27. EFI Output

Example:

```text
workspace/builds/<build-id>/
│
├── EFI/
│   ├── BOOT/
│   └── OC/
│
├── build-manifest.json
├── compatibility-report.json
├── validation-report.json
├── hardware-snapshot.json
└── build.log
```

Sensitive data must be sanitised where appropriate.

---

# 28. Recovery Download

MacLoader should use OpenCore's macOS recovery tooling / Apple's recovery infrastructure rather than redistributing macOS.

Conceptual commands:

```text
macloader recovery list
macloader recovery download tahoe
```

Recovery downloads should include integrity/error checking.

---

# 29. USB Builder

USB writing is destructive and requires special safeguards.

Before formatting/writing:

1. enumerate removable devices;
2. display device path;
3. display vendor/model;
4. display exact capacity;
5. warn that all data will be destroyed;
6. require explicit typed confirmation;
7. refuse ambiguous targets;
8. never automatically select the system disk.

The initial development phases must not require destructive USB writes.

USB writing comes later in the roadmap.

---

# 30. BIOS Configuration

MacLoader should generate a checklist rather than attempt firmware changes.

Example:

```text
macloader bios-guide
```

Output is based on:

- model;
- target macOS;
- selected OpenCore policy.

The checklist should distinguish:

`REQUIRED`

from:

`RECOMMENDED`

Reference EFI repositories must not be blindly copied because BIOS recommendations can differ.

A known working physical-machine baseline should determine final v0.1 guidance.

---

# 31. User Interface

Retain the Libre_Core family resemblance.

Primary interface:

Textual TUI

Secondary interface:

Click CLI

The CLI remains important for:

- tests;
- scripting;
- debugging;
- CI;
- agentic development.

Initial CLI concepts:

```text
macloader probe
macloader support
macloader support --macos tahoe
macloader plan --macos tahoe
macloader build --macos tahoe
macloader validate <build>
macloader recovery download tahoe
macloader usb list
macloader usb build ...
```

---

# 32. Logging

Structured logs should exist from the beginning even though automated diagnostics arrive in v0.2.

Record:

- hardware-detection evidence;
- support decisions;
- resolver decisions;
- component versions;
- downloads;
- checksums;
- config transformations;
- compiler output;
- validation results;
- recovery tooling output;
- USB operations.

Never record unnecessary secrets.

---

# 33. Failure Philosophy

MacLoader should fail loudly rather than improvise.

Unknown components remain UNKNOWN.

Unsupported models must be refused rather than mapped to "similar" machines.

---

# 34. Safety Invariants

These are non-negotiable.

1. Never modify BIOS firmware.
2. Never attempt password bypass.
3. Never silently write internal disks.
4. Never silently format USB devices.
5. Never reuse committed SMBIOS identities.
6. Never advertise untested hardware as SUPPORTED.
7. Never conceal `ocvalidate` failure.
8. Never silently substitute unsupported components.
9. Never overwrite a user's existing EFI without backup/confirmation.
10. Never claim a component works solely because another GitHub EFI includes it.

---

# 35. Testing Strategy

## Unit tests

Required for:

- hardware normalization;
- model matching;
- component matching;
- compatibility states;
- BuildPlan generation;
- dependency ordering;
- checksum validation;
- plist transformations;
- identity redaction;
- USB safety logic.

## Fixture tests

Sanitized hardware snapshots representing:

- T480s known-good baseline;
- T480 iGPU-only baseline;
- T480 + MX150;
- T480 with alternate Wi-Fi;
- unsupported ThinkPad;
- non-Lenovo machine;
- partially detected hardware.

## Golden tests

Known BuildPlans and generated configuration fragments can be compared against reviewed golden outputs.

Golden tests must not contain real SMBIOS identifiers.

## Integration tests

At minimum:

```text
fixture
→ detection normalization
→ compatibility
→ BuildPlan
→ dependency resolution
→ EFI build
→ config.plist
→ ocvalidate
```

Network calls should be mockable.

---

# 36. Physical Hardware Acceptance

Software tests are not sufficient for `SUPPORTED`.

## T480s acceptance

The user's physical T480s becomes the first reference hardware machine.

Before committing its probe result as a fixture, sanitize:

- machine serial;
- UUID;
- MAC address;
- disk serial;
- other unique identifiers.

Required real-machine checks for the selected macOS release should include as applicable:

- OpenCore picker appears;
- Recovery boots;
- macOS installer launches;
- installation completes;
- macOS boots from generated EFI;
- iGPU acceleration;
- correct display resolution;
- brightness control;
- keyboard;
- trackpad;
- TrackPoint;
- battery reporting;
- internal audio;
- microphone;
- Ethernet;
- Wi-Fi;
- Bluetooth;
- USB-A;
- USB-C;
- webcam;
- sleep;
- wake;
- restart;
- shutdown.

Conditional devices such as touchscreen and Thunderbolt receive their own results.

---

# 37. T480 Acceptance

T480 can be developed initially from:

- authoritative OpenCore guidance;
- community research;
- captured hardware fixtures;
- automated tests.

However:

**T480 must remain EXPERIMENTAL until a real physical T480 passes the acceptance suite.**

No README claim of fully supported T480 until that occurs.

---

# 38. Documentation Requirements

Maintain continuously:

- `ENGINEERING_SPEC_V0.1.md`
- `SUPPORT_MATRIX.md`
- `RESEARCH_LEDGER.md`
- `DECISIONS.md`
- `HARDWARE_ACCEPTANCE.md`
- `ARCHITECTURE.md`

---

# 39. Research Sources

At minimum, development should continually compare:

- official OpenCorePkg documentation;
- current Dortania OpenCore Install Guide;
- Acidanthera upstream repositories;
- OpenIntelWireless upstream repositories;
- other primary kext upstream projects;
- current T480 community implementations;
- current T480s community implementations.

Community EFI repositories are secondary evidence.

Current upstream documentation outranks an old EFI README when they conflict.

---

# 40. Licensing

MacLoader's own licence should initially follow the Libre_Core project family unless deliberately changed.

Third-party components must retain their own licences.

Do not copy arbitrary source/configuration from other repositories without confirming its licence and attribution requirements.

---

# 41. Version Roadmap

## v0.0.1 — T480s hardware detection

Deliver:

- project scaffold;
- typed HardwareSnapshot;
- Linux detector;
- basic Windows detector if practical;
- T480s identification;
- sanitized fixtures;
- probe CLI;
- tests.

Exit criterion:

A T480s can be recognised reliably without hardcoded fake output.

## v0.0.2 — T480 detection

Add:

- T480 identification;
- T480 fixture set;
- detection of important T480 variants;
- MX150 presence detection.

Exit criterion:

T480 and T480s are unambiguously distinguished.

## v0.0.3 — Hardware compatibility report

Add:

- support database;
- compatibility engine;
- macOS target selection;
- component statuses;
- readable support report;
- explicit rejection of unknown models.

Exit criterion:

`macloader support --macos <version>` explains what is and is not supported.

## v0.0.4 — OpenCore dependency resolver

Add:

- OpenCore release metadata;
- downloads;
- caching;
- SHA-256 validation;
- dependency manifests;
- kext resolver.

Exit criterion:

MacLoader can deterministically acquire a verified dependency set.

## v0.0.5 — T480s EFI generator

Add:

- clean EFI skeleton;
- ACPI resolver/compiler;
- config.plist generator;
- kext/driver registration;
- SMBIOS policy;
- local identity generation;
- build manifest.

Exit criterion:

A generated T480s EFI passes internal validation.

## v0.0.6 — T480 EFI generator

Add:

- T480 model policy;
- variant policies;
- optional Nvidia MX150 handling.

Exit criterion:

Fixture-generated T480 EFIs pass internal validation.

## v0.0.7 — ocvalidate and sanity pipeline

Add comprehensive validation.

Exit criterion:

Every build must pass matching `ocvalidate` before MacLoader labels it valid.

## v0.0.8 — macOS Recovery downloader

Add legitimate Apple Recovery acquisition.

Exit criterion:

MacLoader can obtain the requested supported recovery environment without redistributing macOS.

## v0.0.9 — USB installer builder

Add guarded removable-device preparation.

Exit criterion:

A generated MacLoader installation USB can be safely produced.

## v0.1.0 — First end-to-end release

Required:

```text
Probe
→ Compatibility
→ Plan
→ Build EFI
→ Validate
→ Download Recovery
→ Build USB
→ Boot physical T480s
→ Install selected macOS
→ Boot installed macOS
→ Pass documented acceptance suite
```

T480 receives SUPPORTED status only if equivalent physical acceptance has occurred.

Otherwise T480 remains EXPERIMENTAL in v0.1.0 while its generator may still ship behind an explicit warning.

---

# 42. Future Roadmap

## v0.2
Boot-log diagnostics and rule-based failure analysis.

## v0.3
Post-install automation.

## v0.4
Additional ThinkPads only after the component architecture has proven itself.

---

# 43. Definition of Done for v0.1

v0.1 is done when MacLoader can take a supported real ThinkPad configuration from hardware detection to a validated installer without requiring the user to manually assemble an EFI.

The resulting configuration must be:

- explainable;
- reproducible;
- versioned;
- validated;
- hardware-aware;
- macOS-version-aware;
- sourced from known upstream components;
- safe against accidental destructive operations.

The project succeeds not because it contains a working T480s EFI.

It succeeds because it contains a system capable of **constructing the correct T480s EFI for the detected supported configuration and explaining how it reached that result.**
