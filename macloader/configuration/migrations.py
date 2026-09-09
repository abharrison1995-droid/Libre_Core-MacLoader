"""Explicit configuration schema migration boundaries."""

from typing import Any, Tuple

from macloader.domain.configuration import ConfigurationIssue, BlockingStage, IssueSeverity, UserConfiguration


def import_configuration(data: Any) -> Tuple[UserConfiguration, Tuple[ConfigurationIssue, ...]]:
    """Import schema 1 or convert a legacy product-only BuildPlan to a draft."""
    if isinstance(data, dict) and data.get("schema_version") == "1" and "option_selections" in data:
        return UserConfiguration.from_dict(data), ()
    if isinstance(data, dict) and {"target_model", "target_macos", "hardware_snapshot_id"}.issubset(data):
        draft = UserConfiguration(
            hardware_snapshot_id=str(data.get("hardware_snapshot_id", "")),
            policy_version="",
        )
        issue = ConfigurationIssue(
            code="LEGACY_PLAN_REQUIRES_REVIEW", field_path="target", severity=IssueSeverity.ERROR,
            blocking_stage=BlockingStage.CONFIGURATION, rule_id="migration.legacy_product_only",
            explanation="Legacy product-only plans do not contain an exact version/build or current option bindings.",
            remediation="Select an exact catalog release and review all current policy selections.",
        )
        return draft, (issue,)
    raise ValueError("configuration has an unknown or unsupported schema")
