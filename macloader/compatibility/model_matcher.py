"""Strict model matching for ThinkPad laptops."""

import logging
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
    if "LENOVO" not in mfg:
        return None, SupportDecision(
            target="system:model",
            state=CompatibilityState.BLOCKED,
            reason=f"Unsupported manufacturer '{snapshot.manufacturer or 'Unknown'}'. MacLoader currently only supports Lenovo ThinkPads.",
            evidence=f"Manufacturer: {snapshot.manufacturer}, Product: {snapshot.product_name}",
        )

    # 1. Match by exact Lenovo 4-character machine type (e.g. 20L7, 20L8 for T480s; 20L5, 20L6 for T480)
    if snapshot.machine_type:
        mt_upper = snapshot.machine_type.strip().upper()
        model = db.get_model_by_machine_type(mt_upper)
        if model:
            return model, SupportDecision(
                target=f"model:{model.id}",
                state=CompatibilityState.EXPERIMENTAL,
                reason=f"Matched supported model '{model.display_name}' by machine type '{mt_upper}'.",
                evidence=f"Machine Type: {mt_upper}, Product: {snapshot.product_name}",
            )

    # 2. Match by exact product name / product version
    prod_name = (snapshot.product_name or "").strip()
    prod_ver = (snapshot.product_version or "").strip()

    for model in db.models.values():
        # Check explicit product names
        for expected_name in model.product_names:
            if expected_name.lower() in prod_name.lower() or expected_name.lower() in prod_ver.lower():
                return model, SupportDecision(
                    target=f"model:{model.id}",
                    state=CompatibilityState.EXPERIMENTAL,
                    reason=f"Matched supported model '{model.display_name}' by product string '{expected_name}'.",
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
