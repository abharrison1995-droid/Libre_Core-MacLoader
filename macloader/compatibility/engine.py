"""Compatibility engine for evaluating HardwareSnapshots against macOS targets."""

from datetime import datetime, timezone
import logging
from typing import List, Optional, Tuple

from macloader.compatibility.model_matcher import match_model
from macloader.database.loader import Database, get_database
from macloader.database.schema import ComponentSchema, ComponentVersionPolicy, ModelSchema
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import (
    CompatibilityReport,
    CompatibilityState,
    ComponentCompatibilityResult,
    SupportDecision,
)
from macloader.domain.hardware import HardwareSnapshot
from macloader.exceptions import UnsupportedMacOSError

logger = logging.getLogger(__name__)


class CompatibilityEngine:
    """Evaluates hardware compatibility against a target macOS version and generates BuildPlans."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_database()

    def evaluate(self, snapshot: HardwareSnapshot, target_macos: str = "sequoia") -> CompatibilityReport:
        """Perform full compatibility evaluation for the given snapshot and target macOS."""
        os_key = target_macos.strip().lower()
        macos_profile = self.db.get_macos(os_key)
        if not macos_profile:
            supported_os = ", ".join(self.db.list_supported_macos())
            raise UnsupportedMacOSError(
                f"Unknown or unsupported macOS target: '{target_macos}'. Supported targets: {supported_os}"
            )

        model_schema, model_decision = match_model(snapshot, self.db)
        component_results: List[ComponentCompatibilityResult] = []
        warnings: List[str] = []

        if not model_schema:
            # Unsupported model: fail loudly and conservatively
            warnings.append(model_decision.reason)
            return CompatibilityReport(
                snapshot_id=snapshot.snapshot_id,
                target_macos=os_key,
                model_id=None,
                model_name=snapshot.product_name or "Unknown",
                overall_state=CompatibilityState.BLOCKED,
                model_decision=model_decision,
                component_results=[],
                warnings=warnings,
                can_generate_build_plan=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # 1. CPU Evaluation
        if snapshot.cpu:
            cpu_gen = snapshot.cpu.generation or "Kaby Lake Refresh"
            is_supported_gen = cpu_gen in model_schema.cpu_generations or any(
                g.lower() in (snapshot.cpu.model_name or "").lower() for g in model_schema.cpu_generations
            )
            cpu_state = CompatibilityState.EXPERIMENTAL if is_supported_gen else CompatibilityState.CONDITIONAL
            component_results.append(
                ComponentCompatibilityResult(
                    category="cpu",
                    component_id="intel-core-processor",
                    component_name=snapshot.cpu.model_name,
                    decision=SupportDecision(
                        target="cpu:intel",
                        state=cpu_state,
                        reason=f"Intel Core processor ({snapshot.cpu.cores}C/{snapshot.cpu.threads}T) compatible with {macos_profile.version_name}.",
                        evidence=f"Model: {snapshot.cpu.model_name}, Gen: {cpu_gen}",
                    ),
                )
            )

        # 2. iGPU Evaluation
        if snapshot.igpu:
            igpu_pci_id = snapshot.igpu.pci.canonical_id if snapshot.igpu.pci else "8086:5917"
            comp = self._match_component_by_pci("graphics", igpu_pci_id) or self.db.get_component("intel-uhd-620")
            if comp and os_key in comp.macos_policies:
                policy = comp.macos_policies[os_key]
                component_results.append(
                    ComponentCompatibilityResult(
                        category="graphics",
                        component_id=comp.id,
                        component_name=snapshot.igpu.name,
                        decision=SupportDecision(
                            target=f"component:{comp.id}",
                            state=policy.state,
                            reason=policy.reason,
                            evidence=f"PCI ID: {igpu_pci_id}",
                            required_actions=policy.required_actions,
                            known_limitations=policy.known_limitations,
                        ),
                    )
                )

        # 3. dGPU Evaluation (e.g. Nvidia MX150 on T480)
        for dgpu in snapshot.dgpus:
            dgpu_pci_id = dgpu.pci.canonical_id if dgpu.pci else "10de:1d10"
            comp = self._match_component_by_pci("graphics", dgpu_pci_id) or self.db.get_component("nvidia-geforce-mx150")
            if comp and os_key in comp.macos_policies:
                policy = comp.macos_policies[os_key]
                component_results.append(
                    ComponentCompatibilityResult(
                        category="graphics_dgpu",
                        component_id=comp.id,
                        component_name=dgpu.name,
                        decision=SupportDecision(
                            target=f"component:{comp.id}",
                            state=policy.state,
                            reason=policy.reason,
                            evidence=f"PCI ID: {dgpu_pci_id}",
                            required_actions=policy.required_actions,
                            known_limitations=policy.known_limitations,
                        ),
                    )
                )
                warnings.append(
                    f"Discrete GPU detected ({dgpu.name} [{dgpu_pci_id}]). Will require disabling via ACPI in future EFI BuildPlan."
                )

        # 4. Audio Evaluation
        if snapshot.audio:
            for audio in snapshot.audio:
                comp = self.db.get_component("realtek-alc257")
                if comp and os_key in comp.macos_policies:
                    policy = comp.macos_policies[os_key]
                    component_results.append(
                        ComponentCompatibilityResult(
                            category="audio",
                            component_id=comp.id,
                            component_name=audio.name,
                            decision=SupportDecision(
                                target=f"component:{comp.id}",
                                state=policy.state,
                                reason=policy.reason,
                                evidence=f"Codec/Device: {audio.codec_name or audio.name}",
                                required_actions=policy.required_actions,
                                known_limitations=policy.known_limitations,
                            ),
                        )
                    )
                    if policy.known_limitations:
                        warnings.extend(policy.known_limitations)

        # 5. Ethernet Evaluation
        if snapshot.ethernet:
            for eth in snapshot.ethernet:
                eth_pci_id = eth.pci.canonical_id if eth.pci else "8086:15d7"
                comp = self._match_component_by_pci("ethernet", eth_pci_id) or self.db.get_component("intel-i219-lm")
                if comp and os_key in comp.macos_policies:
                    policy = comp.macos_policies[os_key]
                    component_results.append(
                        ComponentCompatibilityResult(
                            category="ethernet",
                            component_id=comp.id,
                            component_name=eth.name,
                            decision=SupportDecision(
                                target=f"component:{comp.id}",
                                state=policy.state,
                                reason=policy.reason,
                                evidence=f"PCI ID: {eth_pci_id}",
                                required_actions=policy.required_actions,
                                known_limitations=policy.known_limitations,
                            ),
                        )
                    )

        # 6. Wi-Fi Evaluation
        if snapshot.wifi:
            for wifi in snapshot.wifi:
                wifi_pci_id = wifi.pci.canonical_id if wifi.pci else "8086:24fd"
                comp = self._match_component_by_pci("wifi", wifi_pci_id) or self.db.get_component("intel-ac-8265")
                if comp and os_key in comp.macos_policies:
                    policy = comp.macos_policies[os_key]
                    component_results.append(
                        ComponentCompatibilityResult(
                            category="wifi",
                            component_id=comp.id,
                            component_name=wifi.name,
                            decision=SupportDecision(
                                target=f"component:{comp.id}",
                                state=policy.state,
                                reason=policy.reason,
                                evidence=f"PCI ID: {wifi_pci_id}",
                                required_actions=policy.required_actions,
                                known_limitations=policy.known_limitations,
                            ),
                        )
                    )
                    if policy.known_limitations:
                        warnings.extend(policy.known_limitations)

        # 7. Bluetooth Evaluation
        if snapshot.bluetooth:
            for bt in snapshot.bluetooth:
                bt_usb_id = bt.usb.canonical_id if bt.usb else "8087:0a2b"
                comp = self.db.get_component("intel-bluetooth")
                if comp and os_key in comp.macos_policies:
                    policy = comp.macos_policies[os_key]
                    component_results.append(
                        ComponentCompatibilityResult(
                            category="bluetooth",
                            component_id=comp.id,
                            component_name=bt.name,
                            decision=SupportDecision(
                                target=f"component:{comp.id}",
                                state=policy.state,
                                reason=policy.reason,
                                evidence=f"USB ID: {bt_usb_id}",
                                required_actions=policy.required_actions,
                                known_limitations=policy.known_limitations,
                            ),
                        )
                    )

        # 8. Storage Evaluation
        if snapshot.storage:
            for st in snapshot.storage:
                is_pm981 = "PM981" in (st.model or "").upper() or (st.pci and st.pci.canonical_id in ("144d:a808", "144d:a809"))
                comp_id = "samsung-pm981" if is_pm981 else "standard-nvme"
                comp = self.db.get_component(comp_id)
                if comp and os_key in comp.macos_policies:
                    policy = comp.macos_policies[os_key]
                    component_results.append(
                        ComponentCompatibilityResult(
                            category="storage",
                            component_id=comp.id,
                            component_name=st.model,
                            decision=SupportDecision(
                                target=f"component:{comp.id}",
                                state=policy.state,
                                reason=policy.reason,
                                evidence=f"Model: {st.model}",
                                required_actions=policy.required_actions,
                                known_limitations=policy.known_limitations,
                            ),
                        )
                    )
                    if policy.known_limitations:
                        warnings.extend(policy.known_limitations)

        # 9. Input & Touchscreen Evaluation
        if snapshot.input_devices:
            for inp in snapshot.input_devices:
                if inp.kind == "touchscreen":
                    comp = self.db.get_component("elan-touchscreen")
                    if comp and os_key in comp.macos_policies:
                        policy = comp.macos_policies[os_key]
                        component_results.append(
                            ComponentCompatibilityResult(
                                category="input",
                                component_id=comp.id,
                                component_name=inp.name,
                                decision=SupportDecision(
                                    target=f"component:{comp.id}",
                                    state=policy.state,
                                    reason=policy.reason,
                                    evidence=f"Device: {inp.name}",
                                    required_actions=policy.required_actions,
                                    known_limitations=policy.known_limitations,
                                ),
                            )
                        )

        # Compute Overall Compatibility State
        overall_state = CompatibilityState.EXPERIMENTAL
        has_conditional = False

        for res in component_results:
            if res.decision.state == CompatibilityState.BLOCKED:
                overall_state = CompatibilityState.BLOCKED
                break
            elif res.decision.state == CompatibilityState.CONDITIONAL:
                has_conditional = True

        if overall_state != CompatibilityState.BLOCKED and has_conditional:
            overall_state = CompatibilityState.CONDITIONAL

        can_generate_build_plan = overall_state.is_usable

        return CompatibilityReport(
            snapshot_id=snapshot.snapshot_id,
            target_macos=os_key,
            model_id=model_schema.id,
            model_name=model_schema.display_name,
            overall_state=overall_state,
            model_decision=model_decision,
            component_results=component_results,
            warnings=warnings,
            can_generate_build_plan=can_generate_build_plan,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _match_component_by_pci(self, category: str, canonical_pci_id: str) -> Optional[ComponentSchema]:
        """Find a component definition matching a canonical PCI ID."""
        for comp in self.db.get_components_by_category(category):
            rules = comp.match_rules
            pci_ids = [pid.lower() for pid in rules.get("pci_ids", [])]
            if canonical_pci_id.lower() in pci_ids:
                return comp
        return None

    def generate_build_plan(self, report: CompatibilityReport) -> BuildPlan:
        """Create a preliminary BuildPlan domain object based on a CompatibilityReport."""
        required_capabilities: List[str] = []
        planned_components: List[dict] = []
        unresolved_requirements: List[str] = []

        # Graphics capability (only when an iGPU was actually detected).
        # iGPU results use category "graphics"; dGPU results use "graphics_dgpu" —
        # so this no longer needs to guess based on component_id substrings.
        has_igpu = any(cr.category == "graphics" for cr in report.component_results)
        if has_igpu:
            required_capabilities.append("accelerated_intel_uhd_620")
            planned_components.append({
                "category": "graphics",
                "name": "Intel UHD Graphics 620",
                "driver_requirement": "WhateverGreen.kext",
            })
            unresolved_requirements.append("Exact framebuffer and device-id injection policy deferred to v0.0.5")

        # dGPU handling
        for comp_res in report.component_results:
            if comp_res.category == "graphics_dgpu":
                required_capabilities.append("disable_discrete_gpu")
                planned_components.append({
                    "category": "graphics_dgpu",
                    "name": "Nvidia GeForce MX150 Disabler",
                    "policy": "ACPI SSDT-dGPU-Off and -wegnoegpu",
                })

            elif comp_res.category == "audio":
                required_capabilities.append("alc257_audio")
                planned_components.append({
                    "category": "audio",
                    "name": "Realtek ALC257",
                    "driver_requirement": "AppleALC.kext",
                })
                if report.target_macos == "tahoe":
                    unresolved_requirements.append("Tahoe audio workaround (AppleHDA absent) deferred to v0.0.5")
                else:
                    unresolved_requirements.append("Audio layout-id selection deferred to v0.0.5")

            elif comp_res.category == "ethernet":
                required_capabilities.append("intel_gigabit_ethernet")
                planned_components.append({
                    "category": "ethernet",
                    "name": "Intel Gigabit Ethernet",
                    "driver_requirement": "IntelMausi.kext",
                })

            elif comp_res.category == "wifi":
                required_capabilities.append("intel_wireless_lan")
                planned_components.append({
                    "category": "wifi",
                    "name": "Intel Wi-Fi Adapter",
                    "driver_requirement": "AirportItlwm.kext / itlwm.kext",
                })
                if report.target_macos == "tahoe":
                    unresolved_requirements.append("Tahoe Intel wireless driver stack verification deferred to v0.0.5")

            elif comp_res.category == "storage" and "pm981" in comp_res.component_id:
                required_capabilities.append("nvme_compatibility_fix")
                planned_components.append({
                    "category": "storage",
                    "name": "NVMeFix",
                    "driver_requirement": "NVMeFix.kext",
                })

            elif comp_res.category == "input" and "touchscreen" in comp_res.component_id:
                required_capabilities.append("i2c_touchscreen")
                planned_components.append({
                    "category": "input",
                    "name": "I2C Multitouch Touchscreen",
                    "driver_requirement": "VoodooI2C.kext + VoodooI2CHID.kext",
                })

        return BuildPlan(
            target_model=report.model_name,
            target_macos=report.target_macos,
            hardware_snapshot_id=report.snapshot_id,
            support_state=report.overall_state,
            required_capabilities=required_capabilities,
            planned_components=planned_components,
            unresolved_requirements=unresolved_requirements,
            warnings=report.warnings,
            is_preliminary=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
