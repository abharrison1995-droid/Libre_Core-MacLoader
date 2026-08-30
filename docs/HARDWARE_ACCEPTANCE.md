# Hardware Acceptance

## ThinkPad T480s Reference Machine

**State:** Pending Physical Acceptance (Host is non-ThinkPad `AX16Pro`; testing verified via sanitized fixtures).

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
- [ ] Keyboard backlighting & hotkeys
- [ ] Trackpad multitouch gestures
- [ ] TrackPoint pointing & physical buttons
- [ ] Battery status and power management reporting
- [ ] Internal Realtek ALC257 speakers and headphone jack
- [ ] Internal microphone
- [ ] Intel Gigabit Ethernet (I219-LM)
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
