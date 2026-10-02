# P5–P8 phase plan — archived status

The 2026-09-11 phase playbook is superseded by the current [implementation plan](IMPLEMENTATION_PLAN.md) and [project status](PROJECT_STATUS.md). Its procedure-level P5–P8 checklists and historical test counts no longer describe the current checkout.

## Current disposition

- **P5 / Recovery:** software discovery, acquisition, signed chunklist validation, and persistence contracts exist. The exact 24A335 relationship remains externally blocked; no accepted payload is recorded.
- **P6 / Workflow:** shared CLI/TUI configuration, evidence review, dependency resolution, EFI preparation, Recovery operations, and non-destructive media planning are implemented.
- **P7 / Media:** immutable plans, whole-device identity checks, confirmation/re-enumeration, write/readback guards, and platform boundaries exist. Neither platform has a physically qualified production writer.
- **P8 / Installation:** not performed. Picker/Recovery boot, installation, installed-system boot, and hardware acceptance remain open.

Software checks, fixture behavior, and disposable-image results are separate from the external gates above. See the [first-install runbook](T480S_FIRST_INSTALL_RUNBOOK.md) before physical work. The historical full procedure is available in the prior Git revision if audit detail is needed.
