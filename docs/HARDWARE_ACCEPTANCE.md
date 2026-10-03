# Hardware Acceptance

## ThinkPad T480s Reference Machine

**Candidate:** ThinkPad T480s 20L8 / BIOS N22ET85W 1.62 / Sequoia 15.0 build 24A335.

**State:** No physical acceptance is recorded. Fixture/software verification does not count as physical acceptance; see [current project status](PROJECT_STATUS.md) and the [first-install runbook](T480S_FIRST_INSTALL_RUNBOOK.md).

Before storing a live probe as a test fixture, sanitize:
- system serial number (`serial_number`);
- system UUID (`uuid`);
- network MAC addresses (`mac_address`);
- storage serial numbers (`serial`);
- other unnecessary unique hardware identifiers.

### Physical Acceptance Checklist (Pending Real T480s Execution)

- [ ] OpenCore 1.0.7 picker appears
- [ ] macOS Recovery boots
- [ ] macOS installer launches
- [ ] Installation completes to internal NVMe/SATA
- [ ] Installed macOS boots from generated OpenCore EFI
- [ ] Intel UHD 620 graphics acceleration (Metal & QE/CI)
- [ ] Native display resolution & backlight brightness control
- [ ] Keyboard and hotkeys (backlight only if present)
- [ ] Trackpad multitouch gestures
- [ ] TrackPoint pointing & physical buttons
- [ ] Battery status and power management reporting
- [ ] Internal Realtek ALC257 speakers and headphone jack
- [ ] Internal microphone
- [ ] Intel Gigabit Ethernet (reference evidence: I219-V)
- [ ] Intel Wi-Fi (AC 8265) connectivity
- [ ] Intel Bluetooth pairing & audio
- [ ] USB 3.0 Type-A ports
- [ ] USB Type-C charging and data
- [ ] Integrated Webcam
- [ ] ACPI Sleep (S3) / Wake cycles
- [ ] Clean Restart
- [ ] Clean Shutdown
- [ ] I2C Touchscreen (if fitted)
- [ ] Thunderbolt 3 port (if fitted)

---

## ThinkPad T480 Reference Machine

**State:** Pending Physical Acceptance (`EXPERIMENTAL` in v0.1).

T480 must remain `EXPERIMENTAL` until a physical unit passes the complete acceptance suite above.

## Guided v1 readiness (implementation 2026-10-03)

Run `macloader autoload --readiness` on the actual candidate for a read-only gate
report. It inspects exact-commit GitHub CI jobs, current machine match, private
evidence/decisions, EFI byte bindings, Recovery trust mode, writer qualification,
performed full readback/eject and the proven physical first-install route.
Unknown/missing gates block. This report is informational and cannot grant
writer qualification or internal-disk installation authority.

The current branch supplies a synthetic 20L8/1.62 fixture for regression only.
There is **no new live reference capture or physical result**. The helper
`detection/reference_fixture.py` accepts a supplied live capture, removes unique
identifiers/raw captures, fixes regression identifiers/timestamps and marks the
result as non-evidence. Do not create the real reference fixture until such a
capture is provided and its sanitized diff is reviewed.

Where software observes panel resolution but cannot prove touch capability,
guided mode permits one focused physical confirmation through the existing
HardwareConfirmation contract. It is bound to machine/BIOS/panel scope and cannot
override positive touch evidence, a conflicting observation or a changed panel.
Unknown is never silently treated as non-touch.

Physical readiness is presently blocked by the absent qualified Windows writer
and unperformed live capture/USB/boot campaign. Linux capture and USB correlation
are implemented; the Windows USB firmware-correlation provider remains unavailable
and must not guess routes from PnP enumeration. Engineering import remains
available. Successful synthetic builds/ocvalidate do not resolve these gates.

## Windows writer implementation versus qualification

The native Windows backend and labelled sacrificial-device harness are now
implemented; see [WINDOWS_WRITER_QUALIFICATION.md](WINDOWS_WRITER_QUALIFICATION.md).
The source/Windows-build approval catalog is empty. No actual writer campaign,
T480s evidence capture, installer write or picker/Recovery/OS-return result has
been performed. Software/disposable tests remain distinct from physical evidence.
