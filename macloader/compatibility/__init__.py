"""Compatibility engine and reporting package."""

from macloader.compatibility.model_matcher import match_model
from macloader.compatibility.engine import CompatibilityEngine
from macloader.compatibility.report import (
    render_hardware_snapshot,
    render_compatibility_report,
    render_build_plan,
)

__all__ = [
    "match_model",
    "CompatibilityEngine",
    "render_hardware_snapshot",
    "render_compatibility_report",
    "render_build_plan",
]
