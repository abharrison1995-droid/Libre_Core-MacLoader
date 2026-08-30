"""Fixture-backed hardware detection provider for deterministic testing and offline evaluation."""

import json
from pathlib import Path
from typing import Any, Dict, Union
import yaml

from macloader.detection.base import BaseHardwareProvider
from macloader.domain.hardware import HardwareSnapshot
from macloader.exceptions import HardwareDetectionError


class FixtureHardwareProvider(BaseHardwareProvider):
    """Loads a HardwareSnapshot from a fixture file or preloaded dictionary."""

    def __init__(self, source: Union[str, Path, Dict[str, Any]]):
        self.source = source

    def probe(self) -> HardwareSnapshot:
        """Load and return the HardwareSnapshot from the fixture source."""
        if isinstance(self.source, dict):
            return HardwareSnapshot.from_dict(self.source)

        path = Path(self.source)
        if not path.is_file():
            raise HardwareDetectionError(f"Fixture file does not exist: {path}")

        try:
            content = path.read_text(encoding="utf-8")
            if path.suffix.lower() in (".yaml", ".yml"):
                data = yaml.safe_load(content)
            else:
                data = json.loads(content)
            return HardwareSnapshot.from_dict(data)
        except Exception as e:
            raise HardwareDetectionError(f"Failed to load hardware fixture from {path}: {e}") from e
