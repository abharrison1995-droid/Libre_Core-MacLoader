# Project structure

This is the checked-in structure, not a proposed module map. Use [Architecture](ARCHITECTURE.md) for the responsibilities and [Project status](PROJECT_STATUS.md) for current readiness.

```text
Libre_Core-MacLoader/
├── README.md, START_HERE.md, pyproject.toml
├── macloader/
│   ├── app.py, config.py, orchestrator.py
│   ├── domain/             typed hardware, evidence, plans, config, contracts
│   ├── detection/          Linux, Windows, fixture, normalization, sanitization
│   ├── database/data/      YAML policy, schemas, profiles, tools, dependencies
│   ├── compatibility/      model matching and compatibility reports
│   ├── configuration/      schema, migration, persistence and evaluation
│   ├── workflow/           shared CLI/TUI workflow services
│   ├── dependencies/       resolution, downloads, archive safety and cache
│   ├── build/              EFI, config and ACPI generation/validation
│   ├── toolchain/          trusted tool acquisition and selection
│   ├── recovery/           discovery, acquisition and verification
│   ├── removable/          device adapters, media planning and writer guards
│   ├── identity/           private SMBIOS identity lifecycle
│   ├── evidence/           ACPI and USB evidence models
│   ├── diagnostics/        logging and diagnostics
│   └── ui/                 Click CLI and Textual TUI
├── tests/                  unit/integration tests and sanitized fixtures
├── docs/                   status, design, research, safety and acceptance
├── prompts/                archived development prompts and handoffs
├── tools/                  T480s evidence collection scripts
└── workspace/              ignored local state; not shipped or committed
```

`workspace/` is excluded from Git except for `.gitkeep`. Configuration snapshots, private evidence and identities, tool/download caches, generated EFI, and media images belong in the configured user workspace rather than this checkout.

MacLoader remains a sibling project to Libre_Core-AutoLoader. Its current implementation is native to this repository; the earlier design notes about candidate reuse are historical and do not imply a shared runtime dependency.
