# Start here

MacLoader is a hardware-aware OpenCore preparation tool. Read the [current project status](docs/PROJECT_STATUS.md) for implemented functions, the exact T480s candidate, and its open gates. Read the [first-install runbook](docs/T480S_FIRST_INSTALL_RUNBOOK.md) before any physical preparation.

Install and inspect the local workspace:

```bash
python -m pip install -e ".[dev]"
macloader --help
macloader toolchain status
macloader preflight --json
```

For a supervised configuration on the reference T480s 20L8 / BIOS N22ET85W 1.62:

```bash
macloader config new
macloader config set CONFIG_ID --version 15.0 --build 24A335
macloader config check CONFIG_ID
macloader preflight --config CONFIG_ID --json
```

Use only ACPI and USB evidence captured from that same machine. Keep private captures, identity material, downloads, generated EFI, and media images in the per-user workspace, outside the repository.

The exact Recovery relationship for build 24A335 remains unproven, and neither host has a physically qualified USB writer. Physical boot and installation remain open. Never substitute an OS target or write to a disk without the separate exact-target checkpoint in the runbook.
