"""Base abstraction for hardware detection providers."""

from abc import ABC, abstractmethod
from typing import Optional

from macloader.domain.hardware import HardwareSnapshot


class BaseHardwareProvider(ABC):
    """Abstract interface for system hardware detection."""

    @abstractmethod
    def probe(self) -> HardwareSnapshot:
        """Perform hardware probing and return a normalized HardwareSnapshot."""
        pass
