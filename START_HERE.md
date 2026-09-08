# START HERE — Libre_Core MacLoader

## Current entry point (2026-09-08)

Read [the active implementation plan](docs/IMPLEMENTATION_PLAN.md) first. The hardened foundation and preliminary builder, Recovery and removable-media code are present, with 250 local tests passing, clean mypy and 79.82% branch coverage against a 79% CI gate. **G0 is reopened:** S12 is verified and committed; hosted clean-checkout evidence and the EFI trust/validation boundaries (S01-S04) remain before qualifying T480s/Sequoia. The plan records progress, concrete regression tests and shipping gates. The [review ledger](docs/IMPLEMENTATION_REVIEW.md) preserves earlier findings; old closeout claims are superseded by the plan. Setup and first-tranche instructions below are historical context.

Libre_Core MacLoader is a **new sibling project** to `Libre_Core-AutoLoader`.

## Recommendation: do not create this as a GitHub fork

Create a fresh repository named something like:

`Libre_Core-MacLoader`

Keep the existing AutoLoader repository beside it:

```text
Projects/
├── Libre_Core-AutoLoader/
└── Libre_Core-MacLoader/
```

MacLoader should explicitly treat AutoLoader as an **architectural reference / code donor where appropriate**, not as its Git history parent.

Why:

- AutoLoader's domain is firmware modification; MacLoader's domain is OpenCore/macOS provisioning.
- A literal fork would inherit firmware-specific history, naming, issues, assumptions, releases and potentially irrelevant files.
- We only want selected patterns: project organisation, detection abstractions, database separation, CLI/TUI style, logging/testing conventions and the host/USB workflow philosophy.
- Starting fresh makes it much easier to keep MacLoader's support promise, dependencies and safety boundaries clean.
- If generic utilities are genuinely reusable later, they can be extracted into a shared package instead of coupling the two applications prematurely.

## First implementation tranche

The first Codex run should implement only:

- v0.0.1 — T480s hardware detection
- v0.0.2 — T480 hardware detection
- v0.0.3 — hardware compatibility reporting

Do **not** implement USB writing, macOS installation, firmware changes or a fake EFI builder in the first tranche.

## Read in this order

1. `docs/ENGINEERING_SPEC_V0.1.md`
2. `docs/PROJECT_STRUCTURE.md`
3. `docs/DECISIONS.md`
4. `prompts/CODEX_MASTER_PROMPT_V0.0.1-V0.0.3.md`

The remaining documents are working ledgers/templates for the implementation agent.
