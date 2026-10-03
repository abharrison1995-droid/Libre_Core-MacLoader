# Architecture

This describes the implementation in the current checkout. See [Project status](PROJECT_STATUS.md) for readiness and qualification limits.

## Main data flow

```text
Linux / Windows detector or fixture
          ↓
HardwareSnapshot → normalization and sanitization
          ↓
model/component policy → CompatibilityReport → BuildPlan
          ↓
CLI or Textual UI → shared WorkflowService
          ↓
UserConfiguration + reviewed evidence/options + exact target
          ↓
dependency resolver → pinned downloads/cache → ResolvedDependencySet
          ↓
reviewed profile + private ACPI + trusted toolchain + private identity reference
          ↓
EFI builder → structural checks + matching ocvalidate → redacted manifest
          ↓
Recovery policy/acquisition → non-destructive media plan
```

Recovery and removable-media readiness are separate from EFI build readiness. Exact Recovery and a physically qualified media backend are still external gates.

## Implemented boundaries

| Package | Responsibility |
|---|---|
| `macloader.detection` | Linux, Windows, and fixture providers; normalized hardware snapshots; uncertainty and sanitization. |
| `macloader.database` | Strictly loaded YAML policy for models, components, OS targets, configurations, profiles, dependencies, Recovery, and host tools. |
| `macloader.compatibility` | Evidence-based model/component matching, compatibility reports, and preliminary build plans. |
| `macloader.configuration` and `macloader.workflow` | Versioned configuration state, evidence bindings, evaluation, preflight, orchestration, and shared CLI/TUI operations. |
| `macloader.dependencies` | Capability-based DAG resolution, pinned acquisition, integrity checks, cache coordination, and offline reuse. |
| `macloader.toolchain` | Catalog-verified OpenCore, `ocvalidate`, iASL, and macserial installation/selection. |
| `macloader.build` | Reviewed-profile config generation, machine-bound ACPI processing, EFI assembly, validation, and manifests. |
| `macloader.recovery` | Exact-target discovery policy, bounded acquisition, signed chunklist verification, cache binding, and persistence. |
| `macloader.removable` | Whole-device discovery, immutable media plans, confirmation/re-enumeration contracts, guarded write/readback adapters. |
| `macloader.ui` | Click CLI and keyboard-accessible Textual interface over shared services. |

## Trust and safety boundaries

- Fixtures and imported metadata are not physical evidence. Evidence must be bound to the active snapshot and BIOS and validated from its private source.
- A digest identifies content; it does not prove that content is trusted or belongs to an existing artifact. Build, Recovery, and media stages re-check current artifact relationships.
- Structural EFI validation is distinct from matching-tool `ocvalidate`; both are distinct from physical boot and support acceptance.
- The default removable adapters are not physically qualified. The CLI currently exposes listing and planning, not production USB writes.
- Identity values and raw hardware captures belong in protected per-user storage, not exported configurations or tracked output.

## Reference campaign composition

`Database.campaigns` loads validated `ReferenceCampaign` records from `database/data/campaigns`. A record links the existing model, exact release, reviewed EFI profile, configuration/evidence/Recovery policies and component identities. Linked content contributes to the campaign digest. Conflicting audio defaults, dangling references, firmware/target disagreement and duplicate machine selectors fail closed.

`Database.candidate_campaign(snapshot)` checks vendor, product, exact machine type and normalized BIOS. This is only firmware candidate selection; complete component/observation reconciliation is a separate prerequisite. It does not establish physical acceptance. ACPI import and machine preflight use this lookup rather than independent machine/BIOS literals. Campaigns remain experimental.

New campaigns receive separate versioned composition records. Graphics injection, layout values, dependency pins and logical USB routes stay in their existing reviewed records.
