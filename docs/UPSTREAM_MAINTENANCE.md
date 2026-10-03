# Reviewed upstream candidates

The weekly Monday watcher in `.github/workflows/upstream-candidates.yml` reads
repositories from the existing dependency and toolchain catalogs. It watches all
13 dependency records (OpenCore, Lilu, WhateverGreen, AppleALC, VirtualSMC,
IntelMausi, NVMeFix, VoodooPS2, VoodooI2C, itlwm/AirportItlwm and Bluetooth)
and the linked OpenCore/ACPICA tool sources. Repository URLs, stable release tags,
exact assets, authoritative SHA-256 metadata, bounded downloads and unchanged
source license notices must verify. Missing/changed licenses, ambiguous tags or
assets, hash failures and graph problems block a candidate.

The updater writes an isolated candidate catalog and report. It never edits
production records. It verifies RELEASE and DEBUG assets, proposes one independent
candidate at a time, and validates through the existing Database and dependency
DAG contracts. OpenCore/ACPICA/tool updates are migration reports: EFI baseline,
schema and matching tool versions need separate review. The frozen campaign
remains OpenCore 1.0.7 / Sequoia 15.0 build 24A335.

A candidate must pass:

- Ubuntu/Windows × Python 3.11/3.14 functional tests and mypy;
- the unchanged canonical Ubuntu/Python 3.11 79% branch coverage gate;
- verified acquisition of the entire resolved candidate dependency set;
- deterministic synthetic T480s EFI generation and **real matching ocvalidate**;
- wheel and sdist build/install smoke outside checkout.

The deterministic harness uses actual candidate dependency archives and pinned
host tools. Its ACPI, identity and route evidence are explicitly synthetic.
A valid configuration is not a boot result or hardware qualification. Any failed
matrix, EFI, validator or packaging step prevents proposal readiness/publication.

After every gate succeeds, the workflow publishes a reviewed-candidate report.
Scheduled runs may create a **draft** PR containing only `maintenance/candidates/`;
manual runs default to report-only and need `publish_proposal=true` to create a
PR. Branch push runs for the updater produce reports only. Existing proposal
branches are preserved, never force-pushed; a moved default branch blocks stale
publication. There is no auto-merge, production promotion or automatic support
change. Maintainers review catalog/license changes and choose later policy
migration/physical validation separately. GitHub must permit Actions-created PRs
for publication; reports remain available if that repository setting blocks it.
The schedule becomes active only when the workflow is merged to the default
branch.

## Run locally

```sh
python -m macloader.maintenance.upstream --output /tmp/macloader-upstream-report
python -m pytest -q tests/unit/test_upstream_updates.py
```

The report remains `proposal_ready=false` locally until the actual hosted workflow
gates complete. Do not mark it ready by editing the JSON. Candidate catalogs are
review artifacts, not a second runtime configuration system.

## Implementation verification, 2026-10-03

A live authoritative report found WhateverGreen 1.7.1, VirtualSMC 1.3.8 and
AppleALC 1.9.8 as verified candidates; OpenCore 1.0.8 and ACPICA 20260930 need
separate migrations. WhateverGreen's candidate overlay passed actual dependency
acquisition and the deterministic EFI test with real OpenCore 1.0.7 ocvalidate
locally. Production pins stayed unchanged. Hosted candidate checks are recorded
in the implementation ledger as they complete.
