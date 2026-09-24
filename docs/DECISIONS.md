# Architectural Decisions

## ADR-007 — Guided T480s configuration workflow

**Status:** Accepted by the user, 2026-09-09. Planning defaults; no physical qualification implied.

Use schema-driven Textual menus and Click commands over shared Orchestrator services, resolving reviewed options into the existing BuildPlan, dependency, toolchain, identity and validation contracts. Full implementation detail is in [CONFIGURATION_WORKFLOW_PLAN.md](CONFIGURATION_WORKFLOW_PLAN.md).

The eight approved defaults are:

1. Sequoia first on the user's reference T480s; exact version/build and hardware scope still require concrete confirmation.
2. Reviewed experimental choices require explicit acknowledgement and all applicable evidence, validation and media gates.
3. Advanced controls expose reviewed presets and bounded values only.
4. Unknown optional devices remain visibly unsupported; proceeding requires a reviewed safe installation policy. Unknown critical hardware blocks.
5. Recovery target mismatches block progression and require explicit reselection.
6. Deliberate machine-associated identity reuse, protected local storage and separate private backup; no identity secrets in shareable configurations.
7. Historical policy replay is deferred; saved configurations are re-evaluated against current policy.
8. Windows qualification first, Linux second; Textual installed by default. Media writing remains disabled on each host until its adapter is qualified.

These decisions must not be requested again absent a material proposed change. They do not authorize destructive media operations without the existing fresh target confirmation, nor do they promote any machine to SUPPORTED. Detection, confirmation, evidence completeness, software validation and physical acceptance remain distinct.

---

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

---

## ADR-007 — No build inference for Apple Recovery; evidence-derived gate

**Status:** Accepted (2026-09-24)

No Apple-authoritative, authenticated route binds a Recovery product or payload to Sequoia 15.0 build `24A335`. MacLoader does not infer a build and does not use plaintext discovery. Recovery acquisition stays blocked. Preflight derives the `exact_recovery` check from current, private discovery evidence, and that evidence can never make the gate ready. See [RECOVERY_BUILD_BINDING_DECISION.md](RECOVERY_BUILD_BINDING_DECISION.md).
