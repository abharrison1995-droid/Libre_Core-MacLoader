"""Exception hierarchy for Libre_Core MacLoader."""

from typing import Optional


class MacLoaderError(Exception):
    """Base exception for all MacLoader errors."""


class HardwareDetectionError(MacLoaderError):
    """Raised when hardware detection fails or encounters unexpected errors."""


class ProviderExecutionError(HardwareDetectionError):
    """Raised when an underlying detection command or data source fails."""


class ModelMatchingError(MacLoaderError):
    """Raised when model matching encounters invalid or conflicting hardware data."""


class UnsupportedModelError(ModelMatchingError):
    """Raised when an unsupported or unknown model is detected."""

    def __init__(self, model_name: Optional[str] = None, machine_type: Optional[str] = None, message: Optional[str] = None):
        self.model_name = model_name
        self.machine_type = machine_type
        msg = message or f"Unsupported or unknown hardware model: {model_name or 'Unknown'} (machine type: {machine_type or 'Unknown'})"
        super().__init__(msg)


class DatabaseError(MacLoaderError):
    """Base exception for database loading and schema validation errors."""


class DatabaseValidationError(DatabaseError):
    """Raised when support database YAML files fail schema validation."""


class DatabaseNotFoundError(DatabaseError):
    """Raised when required support database definitions are missing."""


class CompatibilityEvaluationError(MacLoaderError):
    """Raised when compatibility evaluation encounters unexpected failure."""


class UnsupportedMacOSError(CompatibilityEvaluationError):
    """Raised when an unknown or unsupported macOS target version is specified."""


class BuildPlanError(MacLoaderError):
    """Raised when preliminary BuildPlan generation fails."""


class DependencyError(MacLoaderError):
    """Base exception for dependency catalog, resolution, and acquisition errors."""


class DependencyNotFoundError(DependencyError):
    """Raised when a required dependency cannot be found in the catalog."""


class DependencyCycleError(DependencyError):
    """Raised when a circular dependency relationship is detected in the graph."""


class ChecksumMismatchError(DependencyError):
    """Raised when a downloaded or cached artifact fails SHA-256 integrity verification."""


class ArtifactDownloadError(DependencyError):
    """Raised when network acquisition of an upstream dependency artifact fails."""


class ArchiveSecurityError(DependencyError):
    """Raised when an archive contains insecure paths (e.g. directory traversal / absolute paths)."""
