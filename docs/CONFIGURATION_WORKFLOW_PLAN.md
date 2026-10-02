# Configuration workflow — implementation record

This file replaces the September 2026 implementation handoff. The schema-driven workflow is now implemented in `macloader/workflow/`, with Click and Textual as clients. See [Architecture](ARCHITECTURE.md) for the current flow and [Project status](PROJECT_STATUS.md) for readiness.

## Delivered workflow

The shared service creates or resumes a local draft, binds it to a hardware snapshot, applies an exact target and reviewed options, validates evidence and acknowledgements, resolves pinned dependencies, prepares/validates EFI, derives Recovery bindings from current artifacts, and creates non-destructive media plans. It also provides cancellation/resume and configuration revision checks. Configuration and evidence readiness are re-evaluated against current policy.

The available guided EFI profile is the experimental T480s 20L8 / BIOS N22ET85W 1.62 / Sequoia 15.0 build 24A335 candidate. A profile or successful build does not confer physical support. Recovery and physical media gates remain open.

## Accepted defaults

These product decisions remain in force and should not be reopened without a material change:

1. Sequoia first; the exact current candidate is 15.0 / 24A335 on the reference T480s. Other variants do not inherit its qualification.
2. Experimental choices require explicit acknowledgement and all applicable evidence/validation gates.
3. Advanced options are bounded, reviewed values rather than arbitrary plist edits.
4. Unknown optional hardware stays visible and unresolved; unknown critical hardware blocks a build.
5. Recovery mismatches block progression; no silent target substitution.
6. SMBIOS identity is private, machine-associated, deliberately generated/reused, and excluded from shareable output.
7. Saved drafts are re-evaluated against current policy; historical policy replay is deferred.
8. Windows is first for physical media qualification, Linux second. Linux host preparation and software writer code do not change this order. Textual is included by default.

Full decision wording is in [ADR-007](DECISIONS.md#adr-007--guided-t480s-configuration-workflow). Physical media, BIOS, internal-disk, and installation actions retain separate fresh checkpoints; these defaults do not waive them.
