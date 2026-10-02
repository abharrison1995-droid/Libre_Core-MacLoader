# Current project status

Reviewed 2026-10-02 at checkout `636aadb` (`main`). This is the status source of truth for the software and its qualification boundaries. Older reviews, plans, and handoffs are historical unless this page links them as current guidance.

## What the software does

MacLoader is version **0.0.4**, requires Python 3.11+, and provides a Click CLI and Textual TUI over shared workflow services.

- Detects hardware through Linux and Windows providers or JSON fixtures, normalizes and sanitizes the snapshot, then evaluates model/component compatibility and creates an actionable BuildPlan.
- Loads model, component, macOS, profile, dependency, Recovery, and toolchain policy from bundled YAML data.
- Creates, edits, imports, exports, reviews, and resumes schema-driven configurations. It binds selections to the hardware snapshot, validates imported machine evidence, derives the effective profile, and reports blockers through preflight.
- Resolves pinned dependencies, downloads and caches verified artifacts, supports offline reuse, and records provenance.
- Imports and validates private ACPI captures; prepares EFI trees from the reviewed profile, dependency lock, trusted toolchain, and private identity reference; runs structural checks and matching `ocvalidate`; emits redacted build/validation manifests.
- Provides Recovery policy/discovery/acquisition/cache operations with exact-target and Apple-signed chunklist checks. It also provides removable-device discovery and non-destructive media planning with guarded write/readback contracts.

Useful entry points:

```text
macloader probe / support / preflight
macloader config / configure / evidence / identity
macloader deps / toolchain / plan / build / validate
macloader recovery / usb / tui
```

The CLI help is the authoritative command list. CLI and TUI call the shared workflow service; lower-level APIs and test adapters do not grant qualification by themselves.

## Current qualification scope

The guided EFI candidate is narrowly scoped to **ThinkPad T480s 20L8, BIOS N22ET85W 1.62, Sequoia 15.0 build 24A335**. Its profile and software build path exist, but its support state is `EXPERIMENTAL`. Physical boot, Recovery, installation, and hardware acceptance have not been completed. No model is currently `SUPPORTED` by MacLoader acceptance.

The database and compatibility engine also model T480 variants and other macOS policy notes. This is not equivalent to having a reviewed EFI profile, exact selectable release record, or physical qualification for every model/version combination. The exact-target workflow currently freezes Sequoia 15.0/24A335. Do not infer Sonoma, Tahoe, other Sequoia builds, T480, or other T480s machine types from the candidate profile.

## Observed readiness on this checkout

Read-only CLI inspection on 2026-10-02 returned:

- `macloader --version`: `0.0.4`.
- `macloader toolchain status --json`: verified Linux x86_64 toolchain; OpenCore and `ocvalidate` 1.0.7, iASL 20260408, macserial 2.1.8.
- `macloader preflight --json` with no configuration selected: overall `blocked`. Exact Recovery is `externally_blocked`; physical media is `unqualified`; no configuration was selected. Configuration-bound ACPI, USB, identity, and dependency readiness therefore was not established by this invocation.

One preflight wording issue surfaced: with no configuration selected, the report also labels `machine_snapshot` as `blocked` and describes a binding mismatch. In this invocation that check is unevaluated; the missing configuration is the actionable state. Re-run preflight after selecting a configuration to assess its bound snapshot.

The local toolchain result describes this workspace at review time. It is not a claim that another user's workspace has the same installed tools. The full test suite was not run as part of this documentation review.

## Open gates

1. **Exact Recovery:** Apple's current discovery result does not authenticate a relationship between the returned product ID and build 24A335. The reviewed flow returned HTTPS 405; the `AP` response field is a product identifier, not a build. No Recovery payload is accepted on a guess or substituted target.
2. **Machine-bound inputs:** a physical build needs a fresh configuration/snapshot and matching private ACPI and USB observations from the exact reference machine and BIOS. Real SMBIOS identity creation/reuse is a deliberate private workflow checkpoint.
3. **Physical media:** `usb list` and `usb plan` are inspection/planning operations. Linux and Windows physical write backends remain unqualified; production writes are unavailable. Disposable images and guarded writer code do not close this gate.
4. **Physical acceptance:** picker and Recovery boot, installation, installed-system boot, and the hardware checklist remain unperformed. Do not promote support based on generated EFI, structural validation, tests, or `ocvalidate` alone.

No BIOS change, USB write, internal-disk operation, real identity generation, or macOS installation is authorized by this status review.

## Source map

- [Architecture](ARCHITECTURE.md) — implemented modules and data flow.
- [Support matrix](SUPPORT_MATRIX.md) — what is modeled versus physically accepted.
- [Implementation ledger](IMPLEMENTATION_LEDGER.md) — completed software and remaining gates.
- [First-install runbook](T480S_FIRST_INSTALL_RUNBOOK.md) — current physical checkpoints and stop conditions.
- [Hardware acceptance](HARDWARE_ACCEPTANCE.md) — required real-machine evidence.
- [Research ledger](RESEARCH_LEDGER.md) — policy sources and dated research records.
