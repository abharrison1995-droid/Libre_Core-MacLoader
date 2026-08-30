# Project Structure

This is the intended **starting shape**, not a requirement to create every Python module before it has real behaviour.

```text
Libre_Core-MacLoader/
├── START_HERE.md
├── README.md
├── pyproject.toml                  # agent creates during v0.0.1
├── LICENSE                         # choose/confirm before publishing
├── .gitignore
│
├── macloader/
│   ├── domain/
│   ├── detection/
│   ├── database/
│   │   └── data/
│   │       ├── models/
│   │       ├── components/
│   │       └── macos/
│   ├── compatibility/
│   ├── dependencies/
│   ├── opencore/
│   ├── acpi/
│   │   └── sources/
│   ├── recovery/
│   ├── usb/
│   ├── ui/
│   └── diagnostics/
│
├── tests/
│   ├── fixtures/
│   │   ├── t480/
│   │   ├── t480s/
│   │   └── unsupported/
│   ├── unit/
│   ├── integration/
│   └── golden/
│
├── docs/
│   ├── ENGINEERING_SPEC_V0.1.md
│   ├── PROJECT_STRUCTURE.md
│   ├── ARCHITECTURE.md
│   ├── SUPPORT_MATRIX.md
│   ├── RESEARCH_LEDGER.md
│   ├── HARDWARE_ACCEPTANCE.md
│   └── DECISIONS.md
│
├── prompts/
│   └── CODEX_MASTER_PROMPT_V0.0.1-V0.0.3.md
│
└── workspace/
```

## Relationship to Libre_Core-AutoLoader

MacLoader is a **sibling**, not a fork.

Codex should inspect AutoLoader for reusable patterns, especially:

- packaging conventions;
- CLI/TUI style;
- hardware detection abstractions;
- model/database separation;
- orchestration patterns;
- logging;
- tests;
- host/USB workflow concepts.

Do not mechanically copy:

- firmware flashing modules;
- ROM logic;
- firmware model records;
- Coreboot/Libreboot-specific state;
- firmware safety assumptions;
- project history.

If a generic utility is genuinely worth sharing, first copy/adapt it into MacLoader with provenance documented. Only consider a shared library after duplication becomes real rather than hypothetical.
