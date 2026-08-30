"""Runtime configuration and filesystem paths for MacLoader."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = PROJECT_ROOT / "macloader" / "database" / "data"
DEFAULT_WORKSPACE_DIR = PROJECT_ROOT / "workspace"
DEFAULT_CACHE_DIR = DEFAULT_WORKSPACE_DIR / "cache"

DEFAULT_MACOS_TARGET = "sequoia"
SUPPORTED_MACOS_TARGETS = ["sonoma", "sequoia", "tahoe"]
