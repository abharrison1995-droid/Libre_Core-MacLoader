# Codex Master Prompt — Libre_Core MacLoader v0.0.1 → v0.0.3

You are the lead implementation engineer for a new project named:

# Libre_Core-MacLoader

You have access to the user's development machine and should perform the implementation work directly.

This is a LONG, CAREFUL engineering task. Optimise for correctness, maintainability, testing and research quality rather than speed.

Do not stop after producing a plan. Inspect, research, implement, test, review and document the work.

---

# PROJECT CONTEXT

There is an existing sibling project:

https://github.com/abharrison1995-droid/Libre_Core-AutoLoader

Locate the local checkout if one exists.

Libre_Core-AutoLoader is a Python-based ThinkPad firmware automation project using concepts including:

- Python 3.11+
- Textual
- Rich
- Click
- typed/modular Python
- model/database separation
- hardware detection
- orchestration
- CLI/TUI interfaces
- pytest
- mypy
- host/USB workflow

You MAY study and reuse appropriate architectural patterns and generic reusable code from Libre_Core-AutoLoader where licensing and code ownership permit.

DO NOT turn the existing AutoLoader repository into the Hackintosh application.

DO NOT create MacLoader as a Git fork unless the user explicitly overrides the project decision.

DO NOT modify Libre_Core-AutoLoader unless absolutely necessary, and there should presently be no reason to do so.

Create MacLoader as a SEPARATE SIBLING PROJECT.

Preferred local relationship:

```text
parent/
├── Libre_Core-AutoLoader/
└── Libre_Core-MacLoader/
```

MacLoader should record AutoLoader as an architectural reference in its documentation.

Do not push anything to a remote repository unless explicitly authorised by the user.

---

# GOVERNING DOCUMENTS

Read these before implementation:

1. `START_HERE.md`
2. `docs/ENGINEERING_SPEC_V0.1.md`
3. `docs/PROJECT_STRUCTURE.md`
4. `docs/DECISIONS.md`
5. `docs/SUPPORT_MATRIX.md`
6. `docs/RESEARCH_LEDGER.md`
7. `docs/HARDWARE_ACCEPTANCE.md`

The engineering spec and accepted decisions are authoritative unless a discovered technical contradiction requires a documented ADR change.

---

# PROJECT PURPOSE

Libre_Core MacLoader will automate OpenCore/macOS provisioning for explicitly supported ThinkPad laptops.

Initial supported candidates:

- Lenovo ThinkPad T480
- Lenovo ThinkPad T480s

It is NOT currently a generic Hackintosh builder.

Architecture must be component-driven:

```text
hardware detection
→ hardware normalization
→ compatibility engine
→ BuildPlan
→ OpenCore/component resolver
→ generated EFI
→ validation
→ Recovery
→ USB
```

We are NOT implementing the entire pipeline in this first tranche.

---

# PRIMARY ENGINEERING PRINCIPLE

DO NOT implement MacLoader by simply copying or downloading a prebuilt EFI.

Community T480/T480s EFI repositories are research references.

MacLoader must eventually generate its EFI from:

```text
HardwareSnapshot
+
model profile
+
component profiles
+
macOS version policy
+
OpenCore release policy
```

The core intermediate representation will be a typed `BuildPlan`.

Even though BuildPlan execution comes later, design the early compatibility engine so it can naturally produce one later.

---

# FIRST IMPLEMENTATION TRANCHE

Implement and properly test:

## v0.0.1
T480s hardware detection

## v0.0.2
T480 hardware detection

## v0.0.3
Hardware compatibility reporting

Do NOT implement destructive USB writing yet.

Do NOT install macOS.

Do NOT modify firmware.

Do NOT bypass BIOS passwords.

Do NOT write to internal EFI partitions.

Do NOT pretend later milestones are complete.

It is acceptable and desirable to scaffold clean interfaces for future modules without implementing fake behaviour.

---

# MANDATORY RESEARCH

Before encoding Hackintosh-specific facts, inspect CURRENT sources.

Start with:

OpenCorePkg:
https://github.com/acidanthera/OpenCorePkg

OpenCore releases:
https://github.com/acidanthera/OpenCorePkg/releases

Dortania OpenCore Install Guide:
https://dortania.github.io/OpenCore-Install-Guide/

Relevant Kaby Lake laptop configuration guidance:
https://github.com/dortania/OpenCore-Install-Guide/blob/master/config-laptop.plist/kaby-lake.md

Current T480 reference implementations:
Search current maintained T480 OpenCore repositories and compare more than one where practical.

Current T480s reference implementations:
Search current maintained T480s OpenCore repositories and compare more than one where practical.

Libre_Core AutoLoader:
https://github.com/abharrison1995-droid/Libre_Core-AutoLoader

Also inspect primary upstream projects for any component whose compatibility you encode.

Do not treat an old blog post or random EFI as authoritative when current upstream documentation exists.

VERIFY the current OpenCore release rather than trusting an old prompt or hardcoding a version from memory.

---

# RESEARCH LEDGER

Maintain:

`docs/RESEARCH_LEDGER.md`

For every significant platform-specific decision record:

```text
## Question

What were we trying to determine?

## Sources

URL
project
release/tag/commit where applicable
date inspected

## Findings

Concise factual findings.

## Decision

What MacLoader will do.

## Confidence

HIGH / MEDIUM / LOW

## Follow-up

Anything requiring physical testing.
```

Do not litter source code with unsupported folklore.

---

# REPOSITORY BASELINE

Prefer consistency with Libre_Core AutoLoader where reasonable.

Use:

- Python >=3.11
- Click for CLI
- Rich for terminal presentation
- Textual dependency retained for later TUI work
- pytest
- pytest-cov
- mypy

You may add appropriate dependencies such as PyYAML or a schema library, but avoid unnecessary framework bloat.

Every new dependency requires a reason.

---

# PACKAGE SHAPE

Use the provided project structure as the starting point.

Do NOT create dozens of empty fake modules simply because the folders exist.

Only introduce modules with real responsibilities.

Avoid massive files.

Avoid circular dependencies.

Keep hardware probing isolated from Hackintosh policy.

---

# DOMAIN MODEL REQUIREMENTS

Implement a typed `HardwareSnapshot`.

It should be capable of representing at least:

```text
manufacturer
product_name
machine_type
bios_version

cpu

igpu
dgpus

audio
ethernet
wifi
bluetooth

storage

usb_controllers
thunderbolt

input_devices
displays

raw_evidence
```

Prefer structured component objects containing:

```text
vendor
device
vendor_id
device_id
subsystem ids where available
human-readable name
source evidence
```

Do not reduce everything to display strings.

Future compatibility rules must be able to use stable hardware IDs.

---

# PRIVACY / FIXTURE SANITISATION

Hardware snapshots may contain information unique to the user's laptop.

Create sanitisation facilities.

Before a real hardware snapshot is committed as a fixture remove/redact as appropriate:

- system serial number;
- UUID;
- MAC addresses;
- storage serial numbers;
- other unnecessary unique IDs.

PCI vendor/device IDs are NOT sensitive and should remain because they are useful for hardware matching.

Write tests for sanitisation.

---

# DETECTION PROVIDER INTERFACE

Create an abstract/interface-style detector API.

Linux should be the richest initial provider.

Research/use appropriate sources including where available:

- DMI under `/sys/class/dmi`
- `dmidecode`
- `lspci`
- `lsusb`
- sysfs PCI information
- ACPI-related information where useful

Do not make parsing dependent solely on pretty human-readable command output if a more stable machine-readable/source file exists.

External command invocation must:

- have timeouts where sensible;
- capture stdout/stderr;
- produce useful errors;
- be mockable in tests;
- never use shell interpolation unnecessarily.

Implement a Windows provider to the extent practical using supported PowerShell/CIM/PnP facilities.

If full parity cannot yet be achieved, clearly document the missing evidence instead of fabricating it.

---

# FIXTURE PROVIDER

Tests must not require a real T480/T480s.

Create fixture-backed detection/testing support.

Fixtures should represent at minimum:

1. T480s baseline
2. T480 iGPU-only baseline
3. T480 with Nvidia MX150
4. T480/T480s with an alternate WLAN configuration
5. unsupported ThinkPad
6. non-Lenovo system
7. incomplete probe data

Research actual identifiers rather than inventing them.

If an identifier cannot be verified, omit it or mark it as research-needed.

---

# REAL T480s PROBE

The user's T480s is intended to become the first physical reference machine.

If this Codex session is running directly on that T480s or can safely access its hardware information, run:

```text
macloader probe --json
```

once the command exists.

Save a SANITISED snapshot under an appropriate evidence/workspace location.

Do not commit the unsanitised raw snapshot.

Do NOT make configuration changes to the laptop.

Hardware probing must be read-only.

If the current development machine is not the T480s, continue using researched fixtures and document that physical capture remains outstanding.

---

# MODEL MATCHING

Implement explicit T480/T480s model matching.

Requirements:

- use manufacturer + DMI/machine identifiers;
- normalize harmless formatting differences;
- support verified Lenovo identifiers;
- avoid loose substring matching that can confuse neighbouring models;
- distinguish T480 from T480s unambiguously.

UNKNOWN MODEL RULE:

An unsupported laptop must NOT be assigned the nearest profile.

---

# HARDWARE COMPONENT DETECTION

For the initial release, give particular attention to:

## CPU
Identify CPU model/generation sufficiently for compatibility policy.

## Intel iGPU
Identify UHD 620 and relevant PCI IDs.

## Nvidia dGPU
T480 configurations may contain an Nvidia MX150.

Detect whether an Nvidia dGPU is present.

Do NOT implement actual dGPU-disabling ACPI yet.

## Audio
Detect codec/controller information to the extent available.

Do not assume the codec solely from model name.

## Ethernet
Capture controller identity.

## Wi-Fi / Bluetooth
Capture actual hardware.

Do not assume all T480/T480s machines use the same WLAN card.

## Storage
Capture NVMe/SATA controller/model information needed for future compatibility policy.

## Touchscreen
Detect if evidence is available.

## Thunderbolt
Detect controller/presence where practical.

---

# COMPATIBILITY STATES

Implement:

```python
SUPPORTED
CONDITIONAL
EXPERIMENTAL
BLOCKED
UNKNOWN
```

as an enum or equivalently strict representation.

Do not equate "someone on GitHub says it works" with final MacLoader SUPPORTED status.

---

# SUPPORT DATABASE

Prefer declarative YAML/data over hardcoded Python branches.

The schema must be validated at load time.

Malformed support data should fail tests immediately.

---

# MACOS VERSION AWARENESS

The compatibility engine must accept a target OS.

Initial logical targets:

```text
sonoma
sequoia
tahoe
```

Do not assume identical component behaviour across releases.

Do not overclaim exact compatibility until researched and/or tested.

---

# CLI

Implement clean commands along these lines:

```text
macloader --help

macloader probe
macloader probe --json

macloader support
macloader support --macos sequoia
macloader support --macos tahoe

macloader plan --macos tahoe
```

For this tranche, `plan` may generate a high-level preliminary BuildPlan describing future requirements.

It must NOT pretend to have generated an EFI.

Use Rich for human output.

JSON output must also exist for automation.

---

# PRELIMINARY BUILDPLAN

Create the typed BuildPlan domain model now even though EFI execution comes later.

For v0.0.3 it may contain planned requirements such as:

```text
target_model
target_macos
hardware_snapshot_id
required_capabilities
warnings
planned_components
unresolved_requirements
```

Do not invent exact kext/ACPI assets before researching them.

---

# TESTS

Testing is mandatory.

At minimum write unit tests covering:

- T480s matching
- T480 matching
- T480 vs T480s distinction
- unsupported Lenovo model
- non-Lenovo model
- normalization
- Linux parser fixtures
- Windows parser fixtures where implemented
- PCI component normalization
- MX150 detection
- Wi-Fi detection
- incomplete hardware evidence
- sanitisation
- YAML schema loading
- compatibility-state logic
- macOS-version-specific state
- BuildPlan creation
- JSON serialization

Run:

```text
pytest
```

and ensure it passes.

Run:

```text
mypy
```

against the package or appropriately configured targets.

Do not simply silence typing problems with widespread `Any`.

---

# ARCHITECTURAL REVIEW

After implementation, perform a self-review specifically looking for:

1. model-specific hacks leaking into generic detector code;
2. dangerous fuzzy hardware matching;
3. fabricated support claims;
4. poor data/code separation;
5. untested parser assumptions;
6. private hardware identifiers in fixtures;
7. future EFI-builder architecture being boxed in by current shortcuts;
8. giant functions/classes;
9. shell-command injection risks;
10. network or destructive behaviour accidentally introduced.

Fix issues found rather than merely listing them.

---

# REFERENCE EFI RULE

You may inspect existing T480 and T480s repositories in detail.

You SHOULD compare:

- ACPI sets;
- kext sets;
- hardware assumptions;
- SMBIOS choices;
- BIOS recommendations;
- known issues;
- Tahoe/Sequoia differences.

But for this tranche the result of that research belongs primarily in:

```text
RESEARCH_LEDGER.md
SUPPORT_MATRIX.md
DECISIONS.md
```

Do not copy their entire EFI folders into MacLoader.

If you reuse source-level material, verify its licence and record attribution.

---

# IMPORTANT CONTRADICTIONS

Expect references to disagree.

When sources conflict:

1. prefer current upstream documentation where applicable;
2. investigate WHY they differ;
3. record the disagreement;
4. do not arbitrarily pick one;
5. mark physical testing requirements.

The physical T480s will eventually be the first MacLoader acceptance platform.

---

# NO FAKE IMPLEMENTATIONS

Do not create code such as:

```python
def build_efi():
    return True
```

simply to claim a milestone exists.

Later modules may be represented with interfaces/protocols/documentation, but unimplemented behaviour must be explicitly unimplemented.

---

# VERSIONING

Work sequentially.

Create meaningful checkpoints corresponding to:

```text
0.0.1 T480s detection
0.0.2 T480 detection
0.0.3 compatibility reporting
```

You may use git commits locally if the repository is initialized.

Do not push without permission.

At the end, project version should accurately reflect the highest completed milestone.

---

# README

Update the README to state only the actual completed milestone.

Do not advertise automatic EFI building, USB installation, or fully verified Tahoe support before those exist.

Preserve the roadmap from the engineering spec.

---

# EXPECTED END STATE OF THIS RUN

By the end of this task I expect a real working repository that can approximately do:

```bash
macloader probe
macloader probe --json
macloader support --macos sequoia
macloader support --macos tahoe
macloader plan --macos tahoe
```

against fixtures and, when available, the real T480s.

I also expect:

```text
pytest = PASS
mypy = PASS or a clearly justified documented narrow exception
```

and complete engineering documentation.

---

# FINAL HANDOFF DOCUMENT

Before finishing create:

`docs/IMPLEMENTATION_HANDOFF.md`

It must contain:

## Completed
Exactly what works.

## Tests
Commands run and results.

## Hardware evidence
Whether a real T480s probe was obtained.

## Research findings
Most important platform facts discovered.

## Uncertainties
Anything not yet verified.

## Technical debt
Anything intentionally deferred.

## Files changed
High-level map.

## v0.0.4 readiness
What the next agent should do to implement the OpenCore dependency resolver.

## Critical warnings
Any assumptions the next implementation agent must NOT casually change.

This document exists so another frontier coding agent can continue without reconstructing the entire project history.

---

# WORKING STYLE

Do not optimise for speed.

Inspect before changing.

Research before encoding compatibility facts.

Implement in small coherent steps.

Test continuously.

When a test exposes an architectural flaw, fix the architecture rather than patching the test.

Prefer explicit failure over unsafe fallback.

Where a fact genuinely cannot yet be established, model it explicitly as UNKNOWN/EXPERIMENTAL and document what evidence is required.

Begin by:

1. inspecting Libre_Core-AutoLoader;
2. inspecting the current MacLoader workspace/environment;
3. researching current OpenCore/T480/T480s information;
4. reviewing the supplied engineering documents;
5. implementing v0.0.1;
6. testing it;
7. implementing v0.0.2;
8. testing it;
9. implementing v0.0.3;
10. performing the architectural review;
11. producing `IMPLEMENTATION_HANDOFF.md`.

Then return a concise summary of what was actually completed.
