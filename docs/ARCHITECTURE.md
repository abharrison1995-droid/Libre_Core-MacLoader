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

## Observation reconciliation

`configuration/observations.py` projects a sanitized snapshot into existing `HardwareObservation` and `HardwareConfirmation` contracts. Consistent high-confidence observations are accepted automatically; missing/low-confidence facts remain unresolved and conflicting sources cannot be accepted. Observations carry source, provider version, time, snapshot fact digest and BIOS provenance. Repeated unchanged probes preserve their original observation provenance.

`configuration/campaign_match.py` requires the referenced component identities, reviewed panel scope and audio codec subsystem, complete dGPU inventory and observed CPU/storage/input/USB topology. Firmware candidate selection alone cannot unlock it. Display EDID/WMI reports preferred panel resolution, not absence of a touchscreen; missing touch proof stays unknown.

`EvidenceRecord` adds optional `input_scope` and `input_digest` fields. Legacy records load without them and become stale conservatively on hardware changes. Scoped records bind only their declared fact groups; BIOS changes invalidate firmware/USB evidence, while storage changes preserve independent USB/ACPI scopes. Full machine snapshot binding is still required. Evidence from another snapshot is not silently rebound. Reconciliation must run after private machine-session binding and before configuration evaluation.

Windows monitor fields follow Microsoft's [WMI source modes](https://learn.microsoft.com/en-us/windows/win32/wmicoreprov/wmimonitorlistedsupportedsourcemodes) and [display connection enums](https://github.com/MicrosoftDocs/sdk-api/blob/docs/sdk-api-src/content/wingdi/ne-wingdi-displayconfig_video_output_technology.md). Linux panel timing uses the connector EDID described in [kernel EDID documentation](https://kernel.org/doc/html/latest/admin-guide/edid.html). Raw EDID and monitor instance identifiers are not persisted.

## Guided session engine

`autoloader/` contains shared `NextAction`, `AutoloaderSession`, private session store and `AutoloaderService`. `start()` probes the current host, checks the exact firmware campaign, derives a local keyed HMAC from a private stable machine identifier, creates or resumes a configuration and reconciles sanitized observations. Session/configuration IDs and machine bindings are excluded from guided status. No raw machine identity is persisted in a session. When DMI identifiers are unavailable on Linux, the local OS machine-id is used as a conservative OS-bound fallback; reinstalling the OS may require a new session.

Session writes use protected atomic files and compare-and-swap revisions, following existing workflow/configuration storage. Configuration publication precedes session pointer publication; an interrupted initial creation may leave an unused draft, which guided lookup ignores. Each safe action is journaled before execution. Restart marks unfinished attempts interrupted and recomputes prerequisites from configuration/evidence. Campaign/policy changes invalidate acknowledgements and downstream artifacts.

The service advances only registered safe handlers and stops at structured human actions, blockers, cancellation or no progress. Later phases supply capture/build/Recovery/media handlers. The initial engine cannot prepare media and never offers internal installation. Physical facts and destructive decisions cannot be passed as automatic handlers.

The synthetic `t480s_20l8_162_synthetic.json` regression input is explicitly not a live capture, firmware evidence or hardware acceptance. It preserves topology but has no serial, UUID, MAC or storage serial. No real reference fixture has been created.

### Guided clients

`macloader.ui.guided.GuidedApp` renders `NextAction` and submits named semantic choices to `AutoloaderService`. It owns presentation, cancellation and worker dispatch only. The default CLI invokes this guided client; Engineering opens after the guided event loop exits, avoiding nested Textual applications. Campaign snapshots and engineering resume use the same WorkflowService private snapshot store. Guided diagnostics expose internal codes through Engineering, never private paths or identity values in ordinary status.

### Firmware capture providers

AcpiCaptureProvider returns bounded table bytes to WorkflowService.collect_acpi, which owns private storage and existing import metadata. Linux uses kernel `/sys/firmware/acpi/tables`; optional elevation runs fixed isolated standard-library code with no writable target or caller path. Windows provisions only the catalog-pinned [ACPICA 20260408 release](https://github.com/open-acpica/acpica/releases/tag/20260408) tool. Capture checks current private machine binding and scoped hardware/BIOS before attaching evidence. Raw table bytes and subprocess output never enter ordinary logs or guided status. The packaged ACPICA notice accompanies binary provisioning.

### Guided USB physical evidence

`UsbEvidenceCollector` wraps the existing UsbEvidenceSession with private cursor/test-device-token progress. Campaign-linked evidence policy owns physical labels and speed/orientation steps. Restart requires removal before rearming insertion. Linux observation reads direct root-attached kernel USB devices, negotiated speed, controller ancestry, hardwired connection type and firmware-node namespace. Firmware port addresses must independently come from literal `_ADR` values disassembled privately with pinned iASL; kernel hub numbering is never translated into HS/SS names. Raw device serials are transient and only a private keyed token persists. Multiple insertions, wrong speed/device and USB-A correlation failure block. USB-C uncertainty persists and those routes are omitted from the later map. WWAN's reviewed exclusion remains explicit.

Textual polls pending physical steps automatically; the terminal client also watches until interrupted. Provider polling never authorizes a write. Windows's existing PnP snapshots do not prove physical-to-ACPI correlation, so its guided USB capture remains unavailable until a provider can meet the same contract. Engineering import is retained. [Kernel USB sysfs documentation](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-bus-usb) defines connect_type/location/connector properties; these are evidence sources, not permission to guess missing topology.

### Automated build and generated USB map

Safe Tools → private identity decision → final experimental acceptance → dependencies → EFI execution uses existing WorkflowService/Orchestrator. Dependency resolution follows acceptance because the existing resolver requires a complete accepted plan. Campaign output paths derive privately from session/configuration digests. Restart after publication adopts an artifact only after current Recovery/build binding validation. Configuration changes immediately invalidate related artifacts and first-boot checkpoints. Private diagnostics preserve failure detail; ordinary status uses safe messages.

`build.usb_map.generate_usb_map` creates an original codeless AppleUSBHostMergeProperties injector from current USB session plus corroborated firmware `_ADR` values and normalized HardwareSnapshot controller slots. Both USB-A speed companions and reviewed internal routes must be proven, one controller must match, and the 15-port limit is enforced. The frozen P4 policy omits WWAN and Type-C routes. The builder requires source USB digest in the accepted BuildPlan, independently disassembles current firmware, emits MacLoaderUSBMap.kext, adds its codeless OpenCore Kernel entry, and binds source + generated plist digests into existing manifest evidence_digests. No new parallel map database or arbitrary plist input exists. Recovery binding regenerates/checks these same digests. References: [USBMap injector implementation](https://github.com/corpnewt/USBMap/blob/master/USBMap.py), [OpenCore 1.0.7 configuration specification](https://github.com/acidanthera/OpenCorePkg/blob/1.0.7/Docs/Configuration.tex).

Configuration acceptance also checks actual raw ACPI file hashes against captured metadata, rejecting even a structurally valid table mutation; metadata alone cannot satisfy firmware acceptance. ACPI compiler work and identity-bearing EFI files reside in the protected campaign workspace. Synthetic snapshots cannot invoke the production builder through the autoloader; tests explicitly use synthetic artifacts/identity in temporary paths.

Guided media preparation uses `GuidedMediaService` over `RemovableMediaWriter`.
`MediaBindings` distinguishes exact qualification locks from smoke-only records.
The current campaign builds a private EFI + Recovery + Apple boot-layout source;
this cannot enable an unqualified adapter. Explicit target selection is followed
by a fresh erase decision; neither is saved as reusable consent. A persisted
journal reports possible destructive I/O even if readiness fails. Windows-first
writer qualification and safe eject are required before the guided writer runs.
