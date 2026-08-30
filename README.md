# Libre_Core MacLoader

Libre_Core MacLoader automates hardware detection, compatibility evaluation, OpenCore dependency resolution, and provisioning for supported ThinkPad laptops.

It is an independent sibling project to [Libre_Core-AutoLoader](https://github.com/abharrison1995-droid/Libre_Core-AutoLoader).

---

## Current Status: v0.0.4

This repository currently implements **milestones v0.0.1 through v0.0.4**:
- **v0.0.1**: ThinkPad T480s hardware detection (`20L7`, `20L8`)
- **v0.0.2**: ThinkPad T480 hardware detection (`20L5`, `20L6`) & Nvidia MX150 dGPU variant detection
- **v0.0.3**: Version-aware macOS compatibility reporting (Sonoma, Sequoia, Tahoe) and preliminary BuildPlan generation
- **v0.0.4**: OpenCore dependency catalog, DAG graph resolver, SHA-256 integrity verification, and offline cache

> [!NOTE]
> Later milestones (EFI building, macOS recovery downloading, USB installer creation) are scheduled for subsequent milestones (v0.0.5+).

---

## Installation & Setup

```bash
# Clone the repository and install dependencies
git clone https://github.com/abharrison1995-droid/Libre_Core-MacLoader.git
cd Libre_Core-MacLoader

# Install in editable mode
pip install -e .

# Run test suite
pytest
mypy macloader tests
```

---

## CLI Usage

### 1. Probe Hardware
```bash
# Probe current machine
macloader probe

# Output machine-readable JSON
macloader probe --json

# Save sanitized snapshot
macloader probe --sanitize --output my_hardware.json

# Probe from a saved hardware fixture
macloader probe --fixture tests/fixtures/t480s/t480s_baseline.json
```

### 2. Check macOS Compatibility
```bash
# Evaluate compatibility for macOS Sequoia (default)
macloader support --macos sequoia

# Evaluate compatibility for macOS Tahoe
macloader support --macos tahoe

# Evaluate against a specific hardware fixture
macloader support --fixture tests/fixtures/t480/t480_mx150.json --macos sequoia

# Output JSON report
macloader support --macos sequoia --json
```

### 3. Generate Preliminary BuildPlan
```bash
# View preliminary BuildPlan for macOS Tahoe
macloader plan --macos tahoe

# Generate BuildPlan from fixture in JSON format
macloader plan --fixture tests/fixtures/t480s/t480s_baseline.json --macos tahoe --json
```

### 4. Dependency Catalog, Resolution & Cache (v0.0.4)
```bash
# List all verified dependencies in the catalog
macloader deps list
macloader deps list --json

# Side-effect-free dependency resolution for target model and macOS
macloader deps resolve --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia
macloader deps resolve --fixture tests/fixtures/t480s/t480s_baseline.json --macos tahoe --json -o resolved-dependencies.json

# Fetch and cache verified dependency archives with SHA-256 validation
macloader deps fetch --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia

# Operate in strict offline mode from cache
macloader deps fetch --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia --offline

# Verify cache integrity
macloader deps verify --fixture tests/fixtures/t480s/t480s_baseline.json --macos sequoia

# View or clear local cache stats
macloader deps cache
macloader deps cache --clear
```

---

## Architecture Overview

```
Hardware Provider (Linux sysfs / Windows CIM / Fixtures)
       ↓
HardwareSnapshot (Typed domain model)
       ↓
Normalization & Privacy Sanitization
       ↓
Declarative Support Database (YAML schemas for models, components, macOS)
       ↓
Compatibility Engine (Version-aware rules & conservative state policy)
       ↓
CompatibilityReport (Rich terminal presentation & JSON)
       ↓
Preliminary BuildPlan (Required capabilities & future resolution targets)
       ↓
Dependency Resolver & DAG Graph (Topological sort, transitive Lilu resolution)
       ↓
Integrity Verification & Cache (SHA-256 checksums, offline workspace/cache/)
```

---

## Project Roadmap

- [x] **v0.0.1** — ThinkPad T480s hardware detection
- [x] **v0.0.2** — ThinkPad T480 hardware detection & MX150 handling
- [x] **v0.0.3** — Hardware compatibility reporting & preliminary BuildPlan
- [x] **v0.0.4** — OpenCore dependency catalog, resolver, verification & cache
- [ ] **v0.0.5** — T480s OpenCore EFI generator
- [ ] **v0.0.6** — T480 OpenCore EFI generator
- [ ] **v0.0.7** — ocvalidate & structural EFI validation pipeline
- [ ] **v0.0.8** — Legitimate Apple macOS Recovery downloader
- [ ] **v0.0.9** — Guarded USB installer builder
- [ ] **v0.1.0** — End-to-end validated release
