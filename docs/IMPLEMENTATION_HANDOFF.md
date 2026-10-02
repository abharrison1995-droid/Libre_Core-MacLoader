# Historical implementation handoff

This file's original v0.0.1–v0.0.4 handoff described a dependency-only prototype and directed the next phase to create the EFI builder. That direction is obsolete: configuration, EFI, Recovery, and removable-media software now exist.

Use these current documents instead:

- [Project status](PROJECT_STATUS.md) for implemented functionality and observed blockers.
- [Architecture](ARCHITECTURE.md) for the actual package boundaries.
- [Implementation plan](IMPLEMENTATION_PLAN.md) for remaining external qualification work.
- [Support matrix](SUPPORT_MATRIX.md) for the precise experimental scope.

Dependency versions and hashes are sourced from `macloader/database/data/dependencies/catalog.yaml`; the host toolchain is sourced from `macloader/database/data/toolchains/catalog.yaml`. This handoff no longer duplicates those catalogs or its August test counts.
