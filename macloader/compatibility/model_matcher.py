"""Strict model matching for ThinkPad laptops."""

import logging
import re
from typing import Optional, Tuple

from macloader.database.loader import Database
from macloader.database.schema import ModelSchema
from macloader.domain.compatibility import CompatibilityState, SupportDecision
from macloader.domain.hardware import HardwareSnapshot

logger = logging.getLogger(__name__)


def match_model(snapshot: HardwareSnapshot, db: Database) -> Tuple[Optional[ModelSchema], SupportDecision]:
    """Strictly matches a HardwareSnapshot against supported models in the database.

    Enforces strict vendor and machine-type / model identity checks.
    Rejects neighbouring ThinkPads or non-Lenovo PCs conservatively.
    """
    mfg = (snapshot.manufacturer or "").strip().upper()
    if mfg not in {"LENOVO", "LENOVO GROUP LIMITED", "LENOVO LIMITED"}:
        return None, SupportDecision(
            target="system:model",
            state=CompatibilityState.BLOCKED,
            reason=f"Unsupported manufacturer '{snapshot.manufacturer or 'Unknown'}'. MacLoader currently only supports Lenovo ThinkPads.",
            evidence=f"Manufacturer: {snapshot.manufacturer}, Product: {snapshot.product_name}",
        )

    mt_upper = (snapshot.machine_type or "").strip().upper() or None
    prod_name = (snapshot.product_name or "").strip()
    prod_ver = (snapshot.product_version or "").strip()

    # Machine type and product strings are independent evidence.  Never use a
    # substring test here: T480 is a prefix of T480s and silently selecting the
    # shorter policy is worse than returning an explicit unknown result.
    machine_model = db.get_model_by_machine_type(mt_upper) if mt_upper else None
    if mt_upper and machine_model is None:
        return None, SupportDecision(
            target="system:model",
            state=CompatibilityState.UNKNOWN,
            reason=f"Machine type '{mt_upper}' is not in the supported model registry; refusing to infer a policy from names alone.",
            evidence=f"Machine Type: {mt_upper}, Product Name: {prod_name}, Product Version: {prod_ver}",
        )
    product_matches = []
    for model in db.models.values():
        for expected_name in model.product_names:
            if _product_alias_matches(expected_name, prod_name) or _product_alias_matches(expected_name, prod_ver):
                product_matches.append(model)
                break

    unique_product_matches = {model.id: model for model in product_matches}
    if machine_model and unique_product_matches and machine_model.id not in unique_product_matches:
        return None, SupportDecision(
            target="system:model",
            state=CompatibilityState.UNKNOWN,
            reason="Contradictory machine-type and product-string evidence; refusing to choose a model policy.",
            evidence=f"Machine Type: {mt_upper}, Product Name: {prod_name}, Product Version: {prod_ver}",
        )

    if machine_model and (prod_name or prod_ver) and machine_model.id not in unique_product_matches:
        return None, SupportDecision(
            target="system:model",
            state=CompatibilityState.UNKNOWN,
            reason="Machine-type evidence conflicts with the supplied product identity; refusing to choose a model policy.",
            evidence=f"Machine Type: {mt_upper}, Product Name: {prod_name}, Product Version: {prod_ver}",
        )

    if machine_model:
        return machine_model, _matched_decision(machine_model, f"machine type '{mt_upper}'", snapshot)

    if len(unique_product_matches) == 1:
        model = next(iter(unique_product_matches.values()))
        return model, _matched_decision(model, "product string", snapshot)

    if len(unique_product_matches) > 1:
        return None, SupportDecision(
            target="system:model",
            state=CompatibilityState.UNKNOWN,
            reason="Product evidence matches multiple supported model policies; refusing an ambiguous match.",
            evidence=f"Product Name: {prod_name}, Product Version: {prod_ver}",
        )

    # If vendor is Lenovo but machine type or product name does not match supported list
    detected_desc = f"{snapshot.product_name} ({snapshot.product_version or 'Unknown'}) [MT: {snapshot.machine_type or 'Unknown'}]"
    return None, SupportDecision(
        target="system:model",
        state=CompatibilityState.BLOCKED,
        reason=f"Unsupported Lenovo ThinkPad model: {detected_desc}. MacLoader v0.1 supports only ThinkPad T480 and T480s.",
        evidence=f"Detected: {detected_desc}",
    )


def _normalize_alias(value: str) -> str:
    """Normalize a model alias without collapsing meaningful suffixes."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _product_alias_matches(alias: str, value: str) -> bool:
    alias_norm = _normalize_alias(alias)
    value_norm = _normalize_alias(value)
    if not alias_norm or not value_norm:
        return False
    if value_norm == alias_norm:
        return True
    # Lenovo machine codes commonly carry a regional/configuration suffix
    # (20L7CTO1WW). Permit that form only for the four-character code aliases.
    return len(alias_norm) == 4 and value_norm.startswith(alias_norm) and value_norm[4:].isalnum()


def _matched_decision(model: ModelSchema, evidence_label: str, snapshot: HardwareSnapshot) -> SupportDecision:
    return SupportDecision(
        target=f"model:{model.id}",
        state=CompatibilityState.EXPERIMENTAL,
        reason=f"Matched supported model '{model.display_name}' by {evidence_label}.",
        evidence=f"Machine Type: {snapshot.machine_type or 'Unknown'}, Product: {snapshot.product_name or snapshot.product_version or 'Unknown'}",
    )
