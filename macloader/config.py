"""Runtime configuration and filesystem paths for MacLoader."""

from pathlib import Path
import os
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = PROJECT_ROOT / "macloader" / "database" / "data"
def _default_workspace() -> Path:
    configured = os.environ.get("MACLOADER_WORKSPACE")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    preferred = base / "Libre_Core-MacLoader"
    # Some managed/CI hosts expose a read-only home directory.  Keep the
    # default user location on normal hosts, but make the CLI usable without
    # requiring an unrelated environment override.
    probe = base
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if probe.exists() and os.access(probe, os.W_OK):
        return preferred
    return Path(tempfile.gettempdir()) / "Libre_Core-MacLoader"


DEFAULT_WORKSPACE_DIR = _default_workspace()
DEFAULT_CACHE_DIR = DEFAULT_WORKSPACE_DIR / "cache"
DEFAULT_PRIVATE_DIR = DEFAULT_WORKSPACE_DIR / "private"
DEFAULT_IDENTITY_DIR = DEFAULT_PRIVATE_DIR / "identities"
DEFAULT_ACPI_DIR = DEFAULT_PRIVATE_DIR / "acpi"
DEFAULT_TOOLCHAIN_DIR = DEFAULT_WORKSPACE_DIR / "p4-toolchain"

DEFAULT_MACOS_TARGET = "sequoia"
SUPPORTED_MACOS_TARGETS = ["sonoma", "sequoia", "tahoe"]
