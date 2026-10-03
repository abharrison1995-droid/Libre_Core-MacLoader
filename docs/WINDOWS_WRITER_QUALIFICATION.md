# Native Windows writer qualification

Status: **implementation supplied; no physical qualification or production
approval**. Exact software verification results are recorded in draft PR #1. ADR-007 remains Windows-first. PR #1 stays draft. The
known-green starting commit is `4dd066da20f866ddecb3844c451717a3c1c5be7d`.

## Implemented boundary

`WindowsRemovableAdapter` still discovers/selects targets. `WindowsNativeBackend`
implements the existing privileged boundary; `current_adapter()` connects it on
Windows. `GuidedMediaService.qualified` is unchanged. Only a reviewed packaged
approval for the exact source digest, Windows kernel version and AMD64 platform
can make `production_qualified` true. The approval catalog is empty.

The source digest normalizes CRLF to LF, so the reviewed Windows checkout and
installed wheel share a binding; other byte changes invalidate it. It covers the
backend, image generator, qualification code,
adapter and shared writer. Editing any of these revokes matching approval. Injected/test APIs cannot inherit a production approval. A
private report or CLI flag cannot grant production approval. A reviewed record
must reference the physical report hash, review PR and all fifteen physical
case outcomes. Tests, synthetic I/O and generated reports do not supply them.
Approval of the backend is separate from approval of an exact USB erase and from
laptop first-boot/hardware acceptance.

Native I/O uses pointer-width Win32 handles and page-aligned, sector-bounded
buffers. No drive-letter write, diskpart script or localized console parsing is
used. Existing discovery remains structured PowerShell/CIM JSON. BusType USB is
accepted in its string or numeric CIM form. Missing safety flags fail closed.

The confirmed full `RemovableDevice` is re-enumerated before opening and again
immediately before write. The held physical-disk handle independently checks
serial, USB bus, direct-access/removable descriptor, disk number, capacity,
512-byte logical sectors and read-only state. Native system-volume extents and
CIM system/boot flags independently refuse system disks. Every target volume
GUID is correlated by extents, locked before dismount, and retained through
readback. New/changing volumes, ambiguous inventory or lock failure stop I/O.

The private plan becomes a deterministic GPT + one FAT32 ESP containing the
existing EFI and `com.apple.recovery.boot` layout. Metadata files remain bound as
in the existing source manifest. The entire disk stream, including free sectors,
is zeroed/written, flushed and read back using uncached I/O and per-chunk hashes.
This is deliberately slower than file-only readback. Cancellation is checked
between bounded chunks in the guided path. Windows will not automatically mount
the rewritten GPT while old volume locks are held.

A successful readback is followed by flush, **non-persistent native disk offline**,
property refresh and confirmed OFFLINE attribute on that same handle. This is
the implemented safe-eject operation; it does not claim mechanical removal or
power-off. Unsupported/offline failure fails preparation. The medium must then
be physically removed/reconnected for qualification. Old volumes are unlocked
only after offline, or when abandoning an untouched target. Failed writes clear
both GPT/signature regions, flush and offline where possible. If disconnect or
read-only state prevents invalidation, no readiness is published; preserve the
failure report and treat the USB as unsafe to boot.

V1 limitations: 1–128 GiB targets, 512-byte logical sectors, native removable USB
storage only, each FAT32 file <4 GiB, total source limits inherited from the shared
writer. Other formats/media are rejected. Mounted targets remain ineligible under
the existing shared safety contract; do not bypass it or secretly unmount a target
before user selection. Prepare a genuinely unmounted sacrificial target using
Windows' storage UI as a separate reviewed qualification preparation step.

## Microsoft API sources

- [CreateFile physical disks/volumes](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
- [FSCTL_LOCK_VOLUME](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume)
- [FSCTL_DISMOUNT_VOLUME](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_dismount_volume)
- [Volume disk extents](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddvol/ni-ntddvol-ioctl_volume_get_volume_disk_extents)
- [Native device descriptor](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-storage_device_descriptor)
- [Disk geometry](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-ioctl_disk_get_drive_geometry_ex)
- [Microsoft native offline implementation and structure-size usage](https://github.com/microsoft/WSL/blob/master/src/windows/common/disk.cpp)
- [Disk offline attributes](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-set_disk_attributes)

## Software gates before a physical run

Run full pytest with canonical >=79% branch coverage, native and Windows-target
mypy, wheel/sdist and external package smoke. Hosted Linux/Windows × Python
3.11/3.14 jobs and package jobs must pass for the exact new commit. Candidate
workflow must retain complete acquisition/EFI/real matching ocvalidate/package
gates. The harness calls the existing actual `hosted_ci_gate`; there is no
`--assume-green`. Uncommitted software or unavailable GitHub evidence blocks.

Synthetic tests inject the storage API and fault boundary. Independent disposable
regular-file tests check GPT with `sgdisk`, FAT with read-only `fsck.fat`, and
read back long-named Recovery bytes using `mcopy`, when installed. Their absence
is a narrow optional-tool skip; deterministic image/API tests stay required.
None of these tests touches a physical drive or qualifies Windows.

## Physical harness: normal case

Prerequisites: actual administrator Windows host, exact known-green checkout,
Git/gh able to read that commit's hosted jobs, genuinely sacrificial unmounted USB,
no valuable data on it, unrelated/internal disks left present for isolation tests.
No T480s internal disk is ever a qualification target.

Read-only status:

```powershell
python -m macloader.removable.windows_qualification
```

Then, on that approved host only:

```powershell
python -m macloader.removable.windows_qualification --run --report "$env:LOCALAPPDATA\MacLoader\writer-qualification\normal.json"
```

This is a separate Engineering qualification entry point, not guided production
writing. It enumerates eligible USBs, shows model/capacity/anonymized reference,
requires exact selection and deliberate erase confirmation, then uses the shared
immutable-source/confirmation/re-enumeration writer. Its payload is explicitly
**non-bootable qualification data**, not an EFI campaign or Recovery substitute.
It never marks the USB bootable or changes production approval. Ctrl+C triggers
the shared invalidation path. A private report is persisted before crossing I/O,
then records actual normal write/full readback/offline outcome. All other cases
remain `not-run`; existing reports are preserved rather than overwritten.

## Required physical cases

For every case retain a private report, exact commit/backend/platform binding,
redacted diagnostics, how the condition was induced, native observations and
whether write/flush/readback/offline occurred. Controlled fault injection on a
real sacrificial medium must be labelled as injection; synthetic unit tests do
not count as the physical result. Review the procedure for each case before
performing it; do not improvise timing-sensitive disconnects on valuable media.

| Case | Physical procedure/evidence | Required result |
| --- | --- | --- |
| normal-write | Harness normal run on sacrificial USB | Full raw readback and confirmed offline; non-bootable report only |
| exact-target-revalidation | Retain initial and pre-I/O anonymized binding/native checks | Same approved target; no number-only inference |
| disappeared | Remove selected USB between selection and confirmation | Abort; no other disk modified |
| identity-changed | Replace selected USB with different sacrificial device before confirmation | Abort; replacement untouched |
| unexpected-mount | Mount/access selected USB before confirming | Abort; no write |
| read-only | Use hardware protection or separately reviewed exact-target read-only preparation | Abort; no ready publication |
| short-write | Controlled native I/O failure on sacrificial medium; preserve injected/native trace | Fail; attempt invalidation; never ready |
| cancelled | Interrupt bounded write/readback on sacrificial medium | Fail; attempt invalidation; never ready |
| flush-failed | Controlled flush failure with real target and recorded method | Fail; never ready |
| readback-corruption | Controlled mismatch in real readback with recorded method | Fail; invalidate/offline where possible |
| eject-failed | Controlled offline refusal with real target and recorded method | Fail even if readback passed |
| reconnect-success | Remove/reconnect after normal offline result; inspect GPT/FAT and test payload read-only | All bytes/layout intact; still non-bootable |
| reconnect-invalidated | Reconnect failed medium after successful invalidation | No valid GPT boot layout; report remains failed |
| unrelated-usb | Normal run with second sacrificial storage attached; compare its read-only pre/post contents | Only selected medium changed |
| system-disks-present | Normal and reject scenarios with Windows system/internal disks present | System/internal targets never eligible/touched |

The normal harness automatically records only normal-write and its target
revalidation on success. Remaining operator results use the explicit reporting
checkpoint, after the documented case was physically performed:

```powershell
python -m macloader.removable.windows_qualification --record-case reconnect-success --result physical-pass --report "$env:LOCALAPPDATA\MacLoader\writer-qualification\normal.json"
```

This confirms an operator report, not production approval. It rejects changed
source/platform/commit binding and requires the same actual green software gate.
Do not report an unperformed case. Case reports alone cannot demonstrate absence
of damage to other disks: retain actual read-only pre/post comparison evidence.
The harness also supports explicitly selected controlled fault scenarios:

```powershell
python -m macloader.removable.windows_qualification --run --scenario short-write --report "$env:LOCALAPPDATA\MacLoader\writer-qualification\short-write.json"
```

Scenarios: `short-write`, `cancelled`, `flush-failed`, `readback-corruption`,
`eject-failed`. Each requires fresh target selection/erase consent and a new
private report. They wrap real selected-device I/O, introduce a labelled one-shot
fault, and let native invalidation/flush/signature-readback/offline proceed.
A fault scenario passes only if the injection actually triggers, readiness is
withheld and invalidation/offline completes. The report explicitly distinguishes
controlled injection from a real driver fault; it grants no production approval.
Disconnect, replacement, read-only, mount, reconnect and isolation scenarios
still require the corresponding physical action/read-only comparison. The
fault-injection method and evidence must be reviewed before approval.

## Approval and actual T480s campaign

After all fifteen cases have real evidence, review the redacted summary and private
report hash. Add only the narrow versioned packaged approval in a reviewed commit,
re-run all software/hosted gates and use that new exact green commit. Never add
raw USB serials, raw ACPI or private identity material to Git. A changed backend
hash or Windows build requires new applicable qualification review.

Then use the existing Guided Autoloader on the real T480s. Detection must match
20L8/N22ET85W 1.62 without forcing. Capture live ACPI, perform USB movements,
accept the experimental profile and make the private identity decision. Build and
validate generated EFI; select strict Recovery if exact 24A335 is authenticated,
otherwise explicitly choose non-qualifying smoke mode. Windows USB firmware
correlation is still unavailable: a reviewed private evidence capture/import or
Linux collection is required; do not invent Windows topology or assume a cross-OS
session/evidence transfer is automatically valid. Re-evaluate bindings on the
actual host after any transfer.

With approved writer, select/confirm the exact installer USB and require write,
complete readback and offline. `macloader autoload --readiness` must pass all
smoke gates. Follow generated proven-port/F12 instructions, reach picker and
Recovery utilities, erase/install nothing, then return to existing OS. Record
all seven existing human checkpoints. EXPERIMENTAL remains unchanged and internal
installation remains outside scope.
