"""Runtime configuration and filesystem paths for MacLoader."""

from pathlib import Path
import os
import sys

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
    return base / "Libre_Core-MacLoader"


DEFAULT_WORKSPACE_DIR = _default_workspace()
DEFAULT_CACHE_DIR = DEFAULT_WORKSPACE_DIR / "cache"

DEFAULT_MACOS_TARGET = "sequoia"
SUPPORTED_MACOS_TARGETS = ["sonoma", "sequoia", "tahoe"]
