"""Dependency resolver translating BuildPlans and capability requirements into resolved dependency sets."""

from datetime import datetime, timezone
import copy
import logging
from typing import Dict, List, Optional, Tuple

from macloader.database.loader import Database, get_database
from macloader.dependencies.graph import DependencyGraph
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.contracts import canonical_json_digest
from macloader.domain.dependencies import (
    ArtifactVariant,
    ResolvedDependency,
    ResolvedDependencySet,
)
from macloader.exceptions import DependencyNotFoundError, UnsupportedMacOSError

logger = logging.getLogger(__name__)


class DependencyResolver:
    """Resolves BuildPlan capability requirements into topologically sorted, versioned dependencies."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_database()
        self.catalog = self.db.get_dependency_catalog()
        if not self.catalog:
            raise DependencyNotFoundError("Dependency catalog not loaded in database.")
        self._graph = self._build_graph()

    def _build_graph(self) -> DependencyGraph:
        graph = DependencyGraph()
        if self.catalog:
            for spec in self.catalog.dependencies.values():
                graph.add_node(spec.id, spec.dependencies)
        return graph

    def catalog_digest(self) -> str:
        if not self.catalog:
            raise DependencyNotFoundError("Dependency catalog unavailable.")
        return canonical_json_digest({
            "policy_version": self.catalog.policy_version,
            "dependencies": [spec.to_dict() for spec in sorted(self.catalog.dependencies.values(), key=lambda item: item.id)],
        })

    def resolve(
        self,
        plan: BuildPlan,
        variant: ArtifactVariant = ArtifactVariant.RELEASE,
    ) -> ResolvedDependencySet:
        """Resolve all external dependencies required for the supplied BuildPlan and target macOS."""
        if not self.catalog:
            raise DependencyNotFoundError("Dependency catalog unavailable.")
        if not plan.support_state.is_usable or plan.support_state in (CompatibilityState.BLOCKED, CompatibilityState.UNKNOWN):
            raise DependencyNotFoundError(
                "Dependency resolution is blocked because the hardware plan is not actionable "
                f"(state={plan.support_state.value})."
            )
        model = self.db.get_model(plan.target_model)
        if model is None:
            model = next(
                (candidate for candidate in self.db.models.values() if candidate.display_name.lower() == plan.target_model.lower()),
                None,
            )
        if model is None:
            raise DependencyNotFoundError(f"Dependency resolution is blocked because the hardware model is not in the verified catalog: '{plan.target_model}'")
        if not plan.policy_version or plan.policy_version != self.catalog.policy_version:
            raise DependencyNotFoundError("Dependency resolution is blocked because the BuildPlan policy is stale or missing")

        target_macos = plan.target_macos.lower()
        os_profile = self.db.get_macos(target_macos)
        if not os_profile:
            raise UnsupportedMacOSError(f"Unknown or unsupported macOS target: '{plan.target_macos}'")
        if os_profile.status in (CompatibilityState.BLOCKED, CompatibilityState.UNKNOWN):
            raise DependencyNotFoundError(
                f"Dependency resolution is blocked by the macOS policy for '{target_macos}' "
                f"(state={os_profile.status.value})."
            )
        requested_specs: Dict[str, Tuple[str, str]] = {}  # dep_id -> (reason, required_by)
        unresolved: List[str] = list(plan.unresolved_requirements)
        warnings: List[str] = list(plan.warnings)
        known_capabilities = {
            "accelerated_intel_uhd_620", "alc257_audio", "intel_gigabit_ethernet",
            "nvme_compatibility_fix", "intel_wireless_lan", "intel_bluetooth",
            "intel_bluetooth_controller", "i2c_touchscreen", "ps2_trackpad_trackpoint",
            "thinkpad_input", "disable_discrete_gpu",
        }

        # 1. Base Bootloader & SMC Runtime
        requested_specs["opencore"] = ("OpenCore UEFI bootloader runtime environment", "core.runtime")
        requested_specs["virtualsmc"] = ("SMC emulator required for macOS kernel initialization", "core.smc")

        # 2. Map BuildPlan capabilities to dependencies
        for cap in plan.required_capabilities:
            if cap not in known_capabilities:
                unresolved.append(f"Unknown capability '{cap}' has no verified dependency mapping")
                continue
            if cap == "accelerated_intel_uhd_620":
                requested_specs["whatevergreen"] = (
                    "Intel UHD 620 graphics driver and display pipeline patching",
                    "capability:accelerated_intel_uhd_620",
                )

            elif cap == "alc257_audio":
                if target_macos in ("sonoma", "sequoia"):
                    requested_specs["applealc"] = (
                        "Realtek ALC257 High Definition Audio injection via AppleALC",
                        "capability:alc257_audio",
                    )
                else:
                    # Tahoe removed AppleHDA; analogue audio is unresolved
                    unresolved.append(
                        "Tahoe analogue audio: AppleHDA was removed in macOS Tahoe; no verified single-kext solution in catalog"
                    )
                    warnings.append(
                        "macOS Tahoe analogue audio cannot be solved with standard AppleALC; requires v0.0.5 root-patch/VoodooHDA research."
                    )

            elif cap == "intel_gigabit_ethernet":
                requested_specs["intelmausi"] = (
                    "Intel I219-LM / I219-V PCI Express Gigabit Ethernet controller driver",
                    "capability:intel_gigabit_ethernet",
                )

            elif cap == "nvme_compatibility_fix":
                requested_specs["nvmefix"] = (
                    "NVMe controller power management and APST fix for Samsung PM981",
                    "capability:nvme_compatibility_fix",
                )

            elif cap == "intel_wireless_lan":
                if target_macos == "sonoma":
                    requested_specs["airportitlwm"] = (
                        "Intel Wi-Fi native Airport driver for macOS Sonoma",
                        "capability:intel_wireless_lan",
                    )
                elif target_macos == "sequoia":
                    requested_specs["itlwm"] = (
                        "Intel Wi-Fi IO80211 driver for macOS Sequoia",
                        "capability:intel_wireless_lan",
                    )
                else:
                    # Tahoe Wi-Fi
                    requested_specs["itlwm"] = (
                        "Intel Wi-Fi experimental driver for macOS Tahoe",
                        "capability:intel_wireless_lan",
                    )
                    warnings.append(
                        "Intel Wi-Fi on macOS Tahoe remains experimental due to framework modernizations in macOS 26."
                    )

            elif cap in ("intel_bluetooth", "intel_bluetooth_controller"):
                requested_specs["intel_bluetooth_firmware"] = (
                    "Intel Bluetooth firmware upload and patcher",
                    "capability:intel_bluetooth",
                )
                requested_specs["bluetoolfixup"] = (
                    "Bluetooth stack injector for modern macOS (from BrcmPatchRAM project)",
                    "capability:intel_bluetooth",
                )

            elif cap == "i2c_touchscreen":
                requested_specs["voodooi2c"] = (
                    "I2C controller and multitouch HID driver for touchscreen",
                    "capability:i2c_touchscreen",
                )

            elif cap in ("ps2_trackpad_trackpoint", "thinkpad_input"):
                requested_specs["voodoops2"] = (
                    "ThinkPad PS/2 keyboard, trackpad, and TrackPoint controller",
                    "capability:ps2_trackpad_trackpoint",
                )

            elif cap == "disable_discrete_gpu":
                # Discrete GPU is handled via ACPI / boot args in EFI generation; no Nvidia kext
                pass

        # 3. Topologically sort requested dependencies and resolve transitive prerequisites
        direct_ids = list(requested_specs.keys())
        ordered_ids = self._graph.resolve_ordered_set(direct_ids)

        # 4. Construct ResolvedDependency domain objects
        resolved_list: List[ResolvedDependency] = []
        for dep_id in ordered_ids:
            spec = self.catalog.dependencies[dep_id]
            artifact = spec.get_artifact(variant)
            if not artifact:
                raise DependencyNotFoundError(
                    f"Artifact variant '{variant.value}' not defined for dependency '{dep_id}'."
                )

            if dep_id in requested_specs:
                reason, req_by = requested_specs[dep_id]
                is_transitive = False
            else:
                # Transitive prerequisite (e.g. Lilu required by WhateverGreen)
                reason = f"Prerequisite dependency required by downstream plugins/drivers."
                req_by = "transitive.dependency"
                is_transitive = True

            resolved_list.append(
                ResolvedDependency(
                    dependency_id=spec.id,
                    project_name=spec.project_name,
                    version=spec.version,
                    variant=artifact.variant,
                    artifact=copy.deepcopy(artifact),
                    reason=reason,
                    required_by=req_by,
                    is_transitive=is_transitive,
                    subcomponents=list(spec.subcomponents),
                )
            )

        is_complete = len(unresolved) == 0

        catalog_digest = self.catalog_digest()
        return ResolvedDependencySet(
            target_model=plan.target_model,
            target_macos=plan.target_macos,
            policy_version=self.catalog.policy_version,
            plan_digest=plan.canonical_digest(),
            catalog_digest=catalog_digest,
            variant=variant,
            resolved_dependencies=resolved_list,
            unresolved_requirements=unresolved,
            warnings=warnings,
            is_complete=is_complete,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
