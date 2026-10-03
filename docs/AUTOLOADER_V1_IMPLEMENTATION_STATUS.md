# Autoloader v1 implementation status

Implementation branch: `codex/autoloader-v1`; draft PR #1. The master specification
is [AUTOLOADER_V1_MASTER_PLAN.md](AUTOLOADER_V1_MASTER_PLAN.md). This page describes
the new guided implementation; the earlier host observations in Project Status
and the runbook remain historical evidence, not a current T480s capture.

## What is implemented

| Phase | Software status | Physical/external limit |
| --- | --- | --- |
| P0 baseline/CI | Platform-correct required matrix, unchanged 79% canonical gate, package smoke; owner audio layout 86 integrated | Physical campaign needs an exact known-green commit |
| P1 campaign | One linked, validated reference campaign; no copied tuning database | Only reviewed 20L8/N22ET85W-1.62 target qualifies as a candidate |
| P2 observations | Provider facts, confirmations, scoped evidence invalidation; detected components select policy | Unknown/conflicting required facts block; panel touch may need focused physical confirmation |
| P3 session/engine | Private HMAC machine binding, automatic create/resume, journal, CAS, shared NextAction | Placeholder DMI identifiers cannot bind a saved campaign |
| P4 guided decisions | Default Textual/text CLI, one policy acceptance, deliberate private identity reuse/generation, Engineering retained | No real identity was generated during implementation |
| P5 ACPI | Linux kernel capture/elevation and hash-pinned Windows provider; exact DSDT + eleven SSDTs | Live target capture and Windows privilege/tool behavior need actual host evidence |
| P6 USB | Linux insertion/removal wizard, private resume, firmware literal route proof, companion/orientation checks | Genuine port movements required; Windows firmware-correlation provider unavailable; unresolved USB-C omitted |
| P7 software preparation | Automatic trusted tools/cache/dependencies/build, actual generated USB map, structural + matching ocvalidate validation | Fixtures/validator success do not prove boot |
| P8 Recovery | Strict exact qualification retained; explicit separate signed smoke record with full chunk verification | Exact 24A335 remains unproven; smoke uses untrusted metadata and cannot authorize installation |
| P9 media integration | Private boot-layout source, existing guarded writer, safe target selection, fresh erase decision, full readback/eject/invalidation integration | No qualified native Windows backend supplied; ADR-007 physical qualification unperformed; production writes disabled |
| P10 first boot | Proven-route F12 instructions; seven bound human-reported checkpoints; explicit failure/retry | No picker/Recovery/OS-return result performed |
| P11 fixture/gate | Synthetic regression/privacy tests, supplied-capture sanitizer, read-only actual CI/evidence readiness report | Real sanitized reference capture absent; physical gate blocked |
| P12 maintenance | Catalog-derived release monitoring, isolated verified candidates, four-cell candidate CI and report/draft-PR gates | Schedule activates after merge; Actions PR permission needed for publication; no auto-merge |

P9 and P11 cannot be declared physically complete. The hardware v1 definition of
done remains unmet. The profile stays EXPERIMENTAL and no internal installation
capability is introduced.

## Architecture and key files

`HardwareSnapshot → observation reconciliation → ReferenceCampaign → private
AutoloaderSession → NextAction → existing WorkflowService/build/Recovery/writer →
GuidedApp or text CLI`.

- `database/campaigns.py` and the campaign YAML link existing versioned records.
- `configuration/observations.py` and `campaign_match.py` reconcile current facts.
- `autoloader/service.py`, `models.py`, `store.py` own shared resumable semantics.
- `evidence/acpi_capture.py`, `usb_capture.py` collect private machine evidence.
- `build/usb_map.py` produces the actual EFI map; the manifest binds it.
- `recovery/smoke.py` owns the separately typed non-qualifying record.
- `autoloader/media.py` extends existing `removable/writer.py` publication guards.
- `autoloader/first_boot.py` records reports without granting support/installation.
- `autoloader/readiness.py` reads exact actual hosted CI and current artifact gates.
- `maintenance/upstream.py` and the candidate workflow propose reviewed updates.

## Remaining human work

On a healthy matching laptop, guided preparation asks for:

1. Physical non-touch confirmation only if reliable software evidence is absent.
2. Test-device setup and the prompted USB movements, including relevant orientation.
3. One deliberate private identity generation/reuse decision.
4. One acceptance of the experimental profile.
5. Explicit smoke-only Recovery choice if exact qualification remains unavailable.
6. Exact sacrificial USB selection and one erase confirmation **after** writer qualification.
7. Supervised F12/picker/Recovery test, no erase/install, reboot and existing-OS check;
   seven explicit result reports, never inferred evidence.

OS privilege prompts and genuine source/evidence failures may add focused actions.
No machine type, BIOS, Wi-Fi/SSD, UUID, internal option/rule IDs, logical routes or
private paths need typing in normal guided mode. The extra Recovery decision and
physical-result reports are required trust boundaries, beyond the three preparation
acceptances originally proposed.

## Exact next readiness boundary

The current host is not the target laptop. Do not begin a boot campaign yet.
A real candidate capture and port evidence are needed; the fixture cannot supply
them. The Windows-first qualified writer contract still needs an actual native
backend, full writer qualification and safe-eject evidence before production USB
writing can be enabled. Windows USB physical firmware correlation is also absent;
Linux collection or Engineering evidence import is available. These gaps must not
be disguised as physical successes. Exact-build qualification additionally needs
an authenticated 24A335 relationship; smoke mode does not solve it.

Use `macloader autoload --readiness` on the candidate to see current gates. It is
read-only and never supplies destructive consent. A later internal installation
requires a separate phase and fresh exact-target approval.

## Verification record

Phase evidence and hosted run IDs are in [IMPLEMENTATION_LEDGER.md](IMPLEMENTATION_LEDGER.md).
The scoped native UI review returned **ship** at 80×24 and 100×35 after complete
production-copy and expanded/selected USB captures. Its scope is the reviewed
interface; screenshots are synthetic and prove no physical result.

The live maintenance report found verified WhateverGreen 1.7.1, VirtualSMC 1.3.8
and AppleALC 1.9.8 candidates, and separate OpenCore 1.0.8/ACPICA 20260930 migrations.
The first real candidate passed acquisition/reference EFI/real matching ocvalidate
locally. Production pins remain unchanged. A Windows CLI test's reliance on the
runner's real disks was discovered by hosted candidate CI and corrected through
injection; failed CI prevented publication.

Final local integration checks: **585 tests passed, 79.49% branch coverage**;
native and Windows-target mypy passed for 140 files; wheel/sdist build and
outside-checkout smoke checks passed. The explicit pinned-tool reference EFI
and real matching ocvalidate test passed. Hosted verification is recorded in
the implementation ledger.
