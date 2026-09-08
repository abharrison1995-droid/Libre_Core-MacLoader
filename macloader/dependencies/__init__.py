"""OpenCore dependency management, DAG graph resolution, caching, and downloading."""

from macloader.dependencies.graph import DependencyGraph
from macloader.dependencies.cache import CacheManager, compute_file_sha256
from macloader.dependencies.downloader import Downloader
from macloader.dependencies.resolver import DependencyResolver
from macloader.dependencies.archive import (
    DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES,
    DEFAULT_MAX_ARCHIVE_MEMBERS,
    DEFAULT_MAX_ARTIFACT_EXPANDED_BYTES,
    DEFAULT_MAX_BUILD_EXPANDED_BYTES,
    safe_extract_zip,
    validate_zip_archive,
)

__all__ = [
    "DependencyGraph",
    "CacheManager",
    "compute_file_sha256",
    "Downloader",
    "DependencyResolver",
    "validate_zip_archive",
    "safe_extract_zip",
    "DEFAULT_MAX_ARCHIVE_MEMBERS",
    "DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES",
    "DEFAULT_MAX_ARTIFACT_EXPANDED_BYTES",
    "DEFAULT_MAX_BUILD_EXPANDED_BYTES",
]
