"""Apple Recovery acquisition contracts and verification."""

from macloader.recovery.acquirer import (
    RecoveryAcquirer,
    RecoveryAsset,
    RecoveryBundle,
    SUPPORTED_RECOVERY_MATRIX,
    validate_recovery_product_version,
    verify_apple_chunklist,
    verify_recovery_integrity,
)
from macloader.recovery.discovery import AppleRecoveryDiscovery, RecoveryDiscoveryResult

__all__ = [
    "RecoveryAcquirer",
    "RecoveryAsset",
    "RecoveryBundle",
    "SUPPORTED_RECOVERY_MATRIX",
    "validate_recovery_product_version",
    "verify_apple_chunklist",
    "verify_recovery_integrity",
    "AppleRecoveryDiscovery",
    "RecoveryDiscoveryResult",
]
