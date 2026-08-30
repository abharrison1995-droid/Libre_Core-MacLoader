# Architecture

## Core data flow

```text
Hardware provider (sysfs, CIM, fixtures)
      ↓
HardwareSnapshot (typed domain model)
      ↓
Normalization / sanitisation (redacting serials, UUIDs, MACs)
      ↓
Model + component database (declarative YAML schemas)
      ↓
Compatibility engine (conservative, version-aware rules)
      ↓
CompatibilityReport (Rich console & JSON)
      ↓
BuildPlan (preliminary capabilities & requirements)
      ↓
[v0.0.4] Dependency Resolver (BuildPlan → DAG Graph → ResolvedDependencySet)
      ↓
[v0.0.4] Downloader & CacheManager (SHA-256 integrity verification & offline cache)
      ↓
[future v0.0.5+] EFI Builder (OpenCore config.plist, ACPI SSDTs, kext structure)
      ↓
[future v0.0.7+] Validator (ocvalidate & structural checks)
      ↓
[future v0.0.8+] Recovery / USB workflow (guarded disk writing)
```

## Architectural Boundaries

### 1. Detection (`macloader.detection`)
Observes the machine. It does not decide whether hardware is Hackintosh-compatible. Emits typed `HardwareSnapshot` with privacy redaction.

### 2. Database (`macloader.database`)
Stores declarative, strictly schema-validated YAML files for models, components, macOS releases, and upstream dependencies.

### 3. Compatibility (`macloader.compatibility`)
Combines observed hardware with target macOS version policies to produce an explainable `CompatibilityReport` and preliminary `BuildPlan`.

### 4. Dependency Catalog & Resolver (`macloader.dependencies`)
- **Dependency Catalog**: Stores reviewed, pinned upstream releases with exact SHA-256 hashes and download URLs.
- **Dependency Resolver**: Translates `BuildPlan` capabilities into topologically ordered `ResolvedDependencySet` using a DAG graph with cycle detection.
- **Integrity Verification & Cache**: Streams official HTTPS downloads, validates SHA-256 checksums, and provides offline caching under `workspace/cache/`.
- **Side-Effect-Free Planning**: `macloader deps resolve` performs pure resolution without network I/O; `macloader deps fetch` acquires artifacts.

### 5. EFI Generation (`macloader.builder` — v0.0.5+)
Future execution layer. Must consume the `ResolvedDependencySet` and `BuildPlan` rather than embedding ad-hoc model folklore or copying third-party EFI folders.

### 6. USB & Recovery (`macloader.usb`, `macloader.recovery` — v0.0.8+)
Destructive operations live behind explicit safety gates and are not part of early tranches.
