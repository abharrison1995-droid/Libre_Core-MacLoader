# Architectural Decisions

## ADR-001 — MacLoader is a sibling repository, not a fork

**Status:** Accepted

### Decision

Create `Libre_Core-MacLoader` as a fresh repository beside `Libre_Core-AutoLoader`.

AutoLoader is an architectural reference and potential source of selected generic code, not the Git history parent.

### Reasons

1. Firmware modification and OpenCore provisioning are different domains.
2. A fork would import irrelevant firmware history and assumptions.
3. A clean repository gives MacLoader its own dependency, release and safety boundaries.
4. Reuse can still happen deliberately at source/module level.
5. A genuinely shared library can be extracted later if repeated generic code proves worth maintaining.

---

## ADR-002 — T480 and T480s only for v0.1

**Status:** Accepted

No fuzzy support for neighbouring ThinkPads.

---

## ADR-003 — Generated EFI, not prebuilt EFI copying

**Status:** Accepted

Reference EFIs are research inputs and regression evidence. The product's output is generated from detected hardware and versioned policy.

---

## ADR-004 — Physical acceptance gates `SUPPORTED`

**Status:** Accepted

Community reports and automated tests can justify `EXPERIMENTAL` or `CONDITIONAL`, but physical acceptance is required for `SUPPORTED`.

---

## ADR-005 — Detection first

**Status:** Accepted

Implement v0.0.1 through v0.0.3 before OpenCore dependency resolution or EFI generation.

---

## ADR-006 — Declarative Dependency Catalog, Pinned Releases, and Graph-Based Resolution

**Status:** Accepted

### Decision
1. Dependencies are defined declaratively in version-controlled YAML catalogs (`macloader/database/data/dependencies/catalog.yaml`) with strict schema validation.
2. Pinned upstream release tags and exact cryptographic SHA-256 hashes are enforced on every download and cache read. Unpinned "latest" redirect URLs and unverified mirrors are prohibited for production builds.
3. Dependency catalog knowledge is strictly separated from dependency selection: BuildPlan capabilities select only the minimal set of dependencies required for that machine and target OS.
4. Transitive dependencies (e.g. `Lilu` required by `WhateverGreen`, `VirtualSMC`, `AppleALC`, `NVMeFix`) and topological ordering are determined via a Directed Acyclic Graph (DAG) with circular dependency detection.
5. Resolution (`deps resolve`) is strictly side-effect-free and performs zero network I/O; artifact acquisition (`deps fetch`) is a distinct stage that supports safe streaming downloads and fully offline operation.
