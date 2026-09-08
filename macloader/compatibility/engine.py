"""Compatibility engine for evaluating HardwareSnapshots against macOS targets."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

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
from macloader.exceptions import HardwareContractError, UnsupportedMacOSError

logger = logging.getLogger(__name__)


class CompatibilityEngine:
    """Evaluates hardware compatibility against a target macOS version and generates BuildPlans."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_database()

    def evaluate(self, snapshot: HardwareSnapshot, target_macos: str = "sequoia") -> CompatibilityReport:
        """Perform full compatibility evaluation for the given snapshot and target macOS."""
        if snapshot is None or not hasattr(snapshot, "manufacturer"):
            raise HardwareContractError(f"Expected HardwareSnapshot instance, got {type(snapshot).__name__}")

        if not isinstance(target_macos, str):
            raise UnsupportedMacOSError(f"Invalid macOS target: '{target_macos}'. Target must be a string.")

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
        unresolved_requirements: List[str] = []

        if not model_schema:
            # Unsupported model: fail loudly and conservatively
            warnings.append(model_decision.reason)
            blocked_state = CompatibilityState.UNKNOWN if model_decision.state == CompatibilityState.UNKNOWN else CompatibilityState.BLOCKED
            return CompatibilityReport(
                snapshot_id=snapshot.snapshot_id,
                target_macos=os_key,
                model_id=None,
                model_name=snapshot.product_name or "Unknown",
                overall_state=blocked_state,
                model_decision=model_decision,
                component_results=[],
                warnings=warnings,
                unresolved_requirements=unresolved_requirements,
                can_generate_build_plan=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # 1. CPU Evaluation
        if snapshot.cpu:
            cpu_gen = snapshot.cpu.generation
            if not cpu_gen:
                component_results.append(self._unknown_result("cpu", "cpu", snapshot.cpu.model_name, "CPU generation is missing from hardware evidence."))
            else:
                is_intel = (snapshot.cpu.vendor or "").strip().lower() in {"genuineintel", "intel"}
                is_supported_gen = is_intel and (cpu_gen in model_schema.cpu_generations or any(
                    g.lower() in (snapshot.cpu.model_name or "").lower() for g in model_schema.cpu_generations
                ))
                cpu_state = CompatibilityState.EXPERIMENTAL if is_supported_gen else CompatibilityState.UNKNOWN
                component_results.append(
                    ComponentCompatibilityResult(
                        category="cpu",
                        component_id="intel-core-processor",
                        component_name=snapshot.cpu.model_name,
                        decision=SupportDecision(
                            target="cpu:intel",
                            state=cpu_state,
                            reason=f"Intel Core processor ({snapshot.cpu.cores}C/{snapshot.cpu.threads}T) evaluated against {macos_profile.version_name}.",
                            evidence=f"Model: {snapshot.cpu.model_name}, Gen: {cpu_gen}",
                        ),
                    )
                )

        # 2. iGPU Evaluation
        if snapshot.igpu:
            igpu_pci_id = snapshot.igpu.pci.canonical_id if snapshot.igpu.pci else None
            comp = self._match_component_by_pci("graphics", igpu_pci_id) if igpu_pci_id else None
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
            dgpu_pci_id = dgpu.pci.canonical_id if dgpu.pci else None
            comp = self._match_component_by_pci("graphics", dgpu_pci_id) if dgpu_pci_id else None
            if comp and os_key in comp.macos_policies:
                policy = comp.macos_policies[os_key]
                options = getattr(model_schema, "options", None)
                has_mx150 = bool(isinstance(options, dict) and options.get("has_mx150_option", False))
                if comp.id == "nvidia-geforce-mx150" and not has_mx150:
                    component_results.append(self._blocked_result(
                        "graphics_dgpu", comp.id, dgpu.name,
                        "This model policy does not declare an MX150 option; refusing to plan for the detected discrete GPU.",
                    ))
                    continue
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
            else:
                component_results.append(self._unknown_result("graphics_dgpu", "unknown-dgpu", dgpu.name, "Discrete GPU identity is not covered by a verified policy."))

        # 4. Audio Evaluation
        if snapshot.audio:
            for audio in snapshot.audio:
                audio_id = f"{audio.codec_vendor_id}:{audio.codec_device_id}" if audio.codec_vendor_id and audio.codec_device_id else None
                audio_pci_id = audio.pci.canonical_id if audio.pci else None
                comp = self._match_component_by_codec(audio_id or audio_pci_id, audio.codec_name)
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
                else:
                    component_results.append(self._unknown_result("audio", "unknown-audio", audio.name, "Audio codec/controller identity is not covered by a verified policy."))

        # 5. Ethernet Evaluation
        if snapshot.ethernet:
            for eth in snapshot.ethernet:
                eth_pci_id = eth.pci.canonical_id if eth.pci else None
                comp = self._match_component_by_pci("ethernet", eth_pci_id) if eth_pci_id else None
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
                else:
                    component_results.append(self._unknown_result("ethernet", "unknown-ethernet", eth.name, "Ethernet adapter identity is not covered by a verified policy."))

        # 6. Wi-Fi Evaluation
        if snapshot.wifi:
            for wifi in snapshot.wifi:
                wifi_pci_id = wifi.pci.canonical_id if wifi.pci else None
                comp = self._match_component_by_pci("wifi", wifi_pci_id) if wifi_pci_id else None
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
                else:
                    component_results.append(self._unknown_result("wifi", "unknown-wifi", wifi.name, "Wi-Fi adapter identity is not covered by a verified policy."))

        # 7. Bluetooth Evaluation
        if snapshot.bluetooth:
            for bt in snapshot.bluetooth:
                bt_usb_id = bt.usb.canonical_id if bt.usb else None
                comp = self._match_component_by_usb(bt_usb_id)
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
                else:
                    component_results.append(self._unknown_result("bluetooth", "unknown-bluetooth", bt.name, "Bluetooth controller identity is not covered by a verified policy."))

        # 8. Storage Evaluation
        if snapshot.storage:
            for st in snapshot.storage:
                is_pm981 = "PM981" in (st.model or "").upper() or (st.pci and st.pci.canonical_id in ("144d:a808", "144d:a809"))
                comp_id = "samsung-pm981" if is_pm981 else "standard-nvme"
                comp = self.db.get_component(comp_id) if is_pm981 else self._match_storage(st)
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
                else:
                    component_results.append(self._unknown_result("storage", "unknown-storage", st.model, "Storage device identity is not covered by a verified policy."))

        # 9. Input evaluation, including keyboard, trackpad and TrackPoint.
        if snapshot.input_devices:
            for inp in snapshot.input_devices:
                comp = self._match_input_component(inp)
                if comp and os_key in comp.macos_policies:
                    policy = comp.macos_policies[os_key]
                    component_results.append(self._component_result("input", comp, inp.name, policy, f"Device: {inp.name}"))
                    warnings.extend(policy.known_limitations)
                    unresolved_requirements.extend(policy.unresolved_requirements)
                else:
                    component_results.append(self._unknown_result("input", f"unknown-input-{inp.kind}", inp.name, "Input device identity is not covered by a verified policy."))

        # Preserve unknown rather than substituting a familiar ThinkPad part.
        for result in component_results:
            comp = self.db.get_component(result.component_id)
            if comp:
                component_policy = comp.macos_policies.get(os_key)
                if component_policy:
                    unresolved_requirements.extend(component_policy.unresolved_requirements)
                    warnings.extend(component_policy.known_limitations)

        # CPU and graphics are singular fields every real ThinkPad reports —
        # unlike audio/ethernet/wifi/storage below, there is no "confirmed
        # empty" state for them, so they stay unconditionally required.
        always_required_categories = ("cpu", "graphics")
        for category in always_required_categories:
            if not any(r.category == category for r in component_results):
                component_results.append(self._unknown_result(category, f"unknown-{category}", "Unknown", "No verified matching hardware policy was found."))

        # Empty collections are only evidence of absence when the provider says
        # that the corresponding inventory was complete. Older providers do not
        # emit completeness metadata, so remain conservative for essential
        # device classes when it is absent.
        # Never call .get() directly on arbitrary imported raw_evidence types!
        raw_ev = getattr(snapshot, "raw_evidence", None)
        completeness: Dict[str, bool] = {}
        if isinstance(raw_ev, dict):
            inv = raw_ev.get("inventory_status")
            if isinstance(inv, dict):
                for cat, status in inv.items():
                    if isinstance(cat, str) and (type(status) is bool and status is True):
                        completeness[cat] = True
        elif hasattr(snapshot, "get_inventory_status") and callable(snapshot.get_inventory_status):
            try:
                inv_res = snapshot.get_inventory_status()
                completeness = inv_res if isinstance(inv_res, dict) else {}
            except Exception:
                completeness = {}

        inventory_fields = {
            "audio": "audio", "ethernet": "ethernet", "wifi": "wifi",
            "bluetooth": "bluetooth", "storage": "storage", "input": "input_devices",
        }
        for category, field_name in inventory_fields.items():
            items = getattr(snapshot, field_name, [])
            is_empty = not items if isinstance(items, (list, tuple, set, dict)) else True
            if is_empty and completeness.get(category) is not True:
                component_results.append(self._unknown_result(category, f"unknown-{category}-inventory", "Unknown", "Inventory for this device class was not confirmed complete."))

        # Compute overall state from the model policy and every observed
        # component. A model's declarative block/unknown state is authoritative.
        model_policy_state = model_schema.default_macos_status.get(os_key, CompatibilityState.UNKNOWN)
        overall_state = model_policy_state
        if overall_state == CompatibilityState.SUPPORTED:
            overall_state = CompatibilityState.EXPERIMENTAL
        has_conditional = False
        has_unknown = False
        has_blocked = model_policy_state == CompatibilityState.BLOCKED or macos_profile.status == CompatibilityState.BLOCKED
        has_unknown = model_policy_state == CompatibilityState.UNKNOWN or macos_profile.status == CompatibilityState.UNKNOWN

        for res in component_results:
            if res.decision.state == CompatibilityState.BLOCKED:
                has_blocked = True
                break
            elif res.decision.state == CompatibilityState.CONDITIONAL:
                has_conditional = True
            elif res.decision.state == CompatibilityState.UNKNOWN:
                has_unknown = True

        if has_blocked:
            overall_state = CompatibilityState.BLOCKED
        elif has_unknown:
            overall_state = CompatibilityState.UNKNOWN
        elif has_conditional or model_policy_state == CompatibilityState.CONDITIONAL:
            overall_state = CompatibilityState.CONDITIONAL

        can_generate_build_plan = (
            overall_state.is_usable
            and not has_unknown
            and not has_blocked
            and model_decision.state != CompatibilityState.UNKNOWN
            and macos_profile.status.is_usable
        )

        return CompatibilityReport(
            snapshot_id=snapshot.snapshot_id,
            target_macos=os_key,
            model_id=model_schema.id,
            model_name=model_schema.display_name,
            overall_state=overall_state,
            model_decision=model_decision,
            component_results=component_results,
            warnings=warnings,
            unresolved_requirements=unresolved_requirements,
            can_generate_build_plan=can_generate_build_plan,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _get_rule_strings(rules: Dict[str, Any], key: str) -> List[str]:
        """Extract a list of lowercase string values for a given rule key safely."""
        val = rules.get(key)
        if isinstance(val, (list, tuple, set)):
            return [str(item).lower() for item in val if item is not None]
        return []

    def _match_component_by_pci(self, category: str, canonical_pci_id: Optional[str]) -> Optional[ComponentSchema]:
        """Find a component definition matching a canonical PCI ID."""
        if not canonical_pci_id:
            return None
        for comp in self.db.get_components_by_category(category):
            rules = comp.match_rules if isinstance(comp.match_rules, dict) else {}
            pci_ids = self._get_rule_strings(rules, "pci_ids")
            if canonical_pci_id.lower() in pci_ids:
                return comp
        return None

    def _match_component_by_usb(self, canonical_usb_id: Optional[str]) -> Optional[ComponentSchema]:
        if not canonical_usb_id:
            return None
        for comp in self.db.get_components_by_category("bluetooth"):
            rules = comp.match_rules if isinstance(comp.match_rules, dict) else {}
            if canonical_usb_id.lower() in self._get_rule_strings(rules, "usb_ids"):
                return comp
        return None

    def _match_component_by_kind(self, category: str, kind: str) -> Optional[ComponentSchema]:
        for comp in self.db.get_components_by_category(category):
            rules = comp.match_rules if isinstance(comp.match_rules, dict) else {}
            if kind.lower() in self._get_rule_strings(rules, "kinds"):
                return comp
        return None

    def _match_input_component(self, inp: object) -> Optional[ComponentSchema]:
        """Match input policy using both kind and physical bus evidence."""
        kind = str(getattr(inp, "kind", "")).lower()
        bus = str(getattr(inp, "bus", "")).lower()
        for comp in self.db.get_components_by_category("input"):
            rules = comp.match_rules if isinstance(comp.match_rules, dict) else {}
            kinds = self._get_rule_strings(rules, "kinds")
            if kind not in kinds:
                continue
            allowed_buses = self._get_rule_strings(rules, "buses")
            if allowed_buses and bus not in allowed_buses:
                continue
            return comp
        return None

    def _match_component_by_codec(self, codec_id: Optional[str], codec_name: Optional[str]) -> Optional[ComponentSchema]:
        for comp in self.db.get_components_by_category("audio"):
            rules = comp.match_rules if isinstance(comp.match_rules, dict) else {}
            codec_ids = self._get_rule_strings(rules, "codec_ids")
            if codec_id and codec_id.lower() in codec_ids:
                return comp
            codec_names = self._get_rule_strings(rules, "codec_names")
            if codec_name and any(cn in codec_name.lower() for cn in codec_names):
                return comp
            if codec_name and "alc257" in codec_name.lower() and comp.id == "realtek-alc257":
                return comp
        return None

    def _match_storage(self, storage: object) -> Optional[ComponentSchema]:
        model = getattr(storage, "model", "").lower()
        kind = getattr(storage, "kind", "").lower()
        pci = getattr(storage, "pci", None)
        pci_id = pci.canonical_id.lower() if pci else None
        device_class = (pci.device_class or "")[:4].lower() if pci else None
        for comp in self.db.get_components_by_category("storage"):
            rules = comp.match_rules if isinstance(comp.match_rules, dict) else {}
            pci_ids = self._get_rule_strings(rules, "pci_ids")
            if pci_id and pci_id in pci_ids:
                return comp
            device_classes = [c[:4] for c in self._get_rule_strings(rules, "device_classes")]
            if device_class and device_class in device_classes:
                return comp
            patterns = [p.replace("*", "") for p in self._get_rule_strings(rules, "model_patterns")]
            if any(pattern and pattern in model for pattern in patterns):
                return comp
            kinds = self._get_rule_strings(rules, "kinds")
            if kind and kind in kinds:
                return comp
        return None

    def _component_result(self, category: str, comp: ComponentSchema, name: str, policy: ComponentVersionPolicy, evidence: str) -> ComponentCompatibilityResult:
        return ComponentCompatibilityResult(
            category=category,
            component_id=comp.id,
            component_name=name,
            decision=SupportDecision(
                target=f"component:{comp.id}", state=policy.state, reason=policy.reason,
                evidence=evidence, required_actions=policy.required_actions, known_limitations=policy.known_limitations,
            ),
        )

    def _unknown_result(self, category: str, component_id: str, name: str, reason: str) -> ComponentCompatibilityResult:
        return ComponentCompatibilityResult(
            category=category, component_id=component_id, component_name=name,
            decision=SupportDecision(target=f"component:{component_id}", state=CompatibilityState.UNKNOWN, reason=reason),
        )

    def _blocked_result(self, category: str, component_id: str, name: str, reason: str) -> ComponentCompatibilityResult:
        return ComponentCompatibilityResult(
            category=category, component_id=component_id, component_name=name,
            decision=SupportDecision(target=f"component:{component_id}", state=CompatibilityState.BLOCKED, reason=reason),
        )

    def generate_build_plan(self, report: CompatibilityReport) -> BuildPlan:
        """Create a preliminary BuildPlan domain object based on a CompatibilityReport."""
        required_capabilities: List[str] = []
        planned_components: List[dict] = []
        unresolved_requirements: List[str] = list(report.unresolved_requirements)
        required_capability_set = set()

        def add_capability(capability: str) -> None:
            if capability not in required_capability_set:
                required_capability_set.add(capability)
                required_capabilities.append(capability)

        # Graphics capability (only when an iGPU was actually detected).
        # iGPU results use category "graphics"; dGPU results use "graphics_dgpu" —
        # so this no longer needs to guess based on component_id substrings.
        has_igpu = any(
            cr.category == "graphics"
            and cr.component_id == "intel-uhd-620"
            and cr.decision.state != CompatibilityState.UNKNOWN
            for cr in report.component_results
        )
        if has_igpu:
            add_capability("accelerated_intel_uhd_620")
            planned_components.append({
                "category": "graphics",
                "name": "Intel UHD Graphics 620",
                "driver_requirement": "WhateverGreen.kext",
            })
            unresolved_requirements.append("Exact framebuffer and device-id injection policy deferred to v0.0.5")

        # dGPU handling
        for comp_res in report.component_results:
            if comp_res.category == "graphics_dgpu" and comp_res.decision.state != CompatibilityState.UNKNOWN:
                add_capability("disable_discrete_gpu")
                planned_components.append({
                    "category": "graphics_dgpu",
                    "name": "Nvidia GeForce MX150 Disabler",
                    "policy": "ACPI SSDT-dGPU-Off and -wegnoegpu",
                })

            elif comp_res.category == "audio" and comp_res.decision.state != CompatibilityState.UNKNOWN:
                add_capability("alc257_audio")
                planned_components.append({
                    "category": "audio",
                    "name": "Realtek ALC257",
                    "driver_requirement": "AppleALC.kext",
                })
                if report.target_macos == "tahoe":
                    unresolved_requirements.append("Tahoe audio workaround (AppleHDA absent) deferred to v0.0.5")
                else:
                    unresolved_requirements.append("Audio layout-id selection deferred to v0.0.5")

            elif comp_res.category == "ethernet" and comp_res.decision.state != CompatibilityState.UNKNOWN:
                add_capability("intel_gigabit_ethernet")
                planned_components.append({
                    "category": "ethernet",
                    "name": "Intel Gigabit Ethernet",
                    "driver_requirement": "IntelMausi.kext",
                })

            elif comp_res.category == "wifi" and comp_res.decision.state != CompatibilityState.UNKNOWN:
                add_capability("intel_wireless_lan")
                planned_components.append({
                    "category": "wifi",
                    "name": "Intel Wi-Fi Adapter",
                    "driver_requirement": "AirportItlwm.kext / itlwm.kext",
                })
                if report.target_macos == "tahoe":
                    unresolved_requirements.append("Tahoe Intel wireless driver stack verification deferred to v0.0.5")

            elif comp_res.category == "storage" and comp_res.decision.state != CompatibilityState.UNKNOWN and "pm981" in comp_res.component_id:
                add_capability("nvme_compatibility_fix")
                planned_components.append({
                    "category": "storage",
                    "name": "NVMeFix",
                    "driver_requirement": "NVMeFix.kext",
                })

            elif comp_res.category == "input" and comp_res.decision.state != CompatibilityState.UNKNOWN and "touchscreen" in comp_res.component_id:
                add_capability("i2c_touchscreen")
                planned_components.append({
                    "category": "input",
                    "name": "I2C Multitouch Touchscreen",
                    "driver_requirement": "VoodooI2C.kext + VoodooI2CHID.kext",
                })

            elif comp_res.category == "input" and comp_res.decision.state != CompatibilityState.UNKNOWN and comp_res.component_id == "thinkpad-trackpad-trackpoint":
                add_capability("ps2_trackpad_trackpoint")
                planned_components.append({
                    "category": "input",
                    "name": comp_res.component_name,
                    "driver_requirement": "VoodooPS2Controller.kext",
                })

            elif comp_res.category == "bluetooth" and comp_res.decision.state != CompatibilityState.UNKNOWN:
                add_capability("intel_bluetooth")
                planned_components.append({
                    "category": "bluetooth",
                    "name": comp_res.component_name,
                    "driver_requirement": "IntelBluetoothFirmware.kext + BlueToolFixup.kext",
                })

        dependency_catalog = self.db.get_dependency_catalog()
        return BuildPlan(
            target_model=report.model_name,
            target_macos=report.target_macos,
            hardware_snapshot_id=report.snapshot_id,
            support_state=report.overall_state,
            required_capabilities=required_capabilities,
            planned_components=planned_components,
            unresolved_requirements=unresolved_requirements,
            warnings=report.warnings,
            is_actionable=report.can_generate_build_plan,
            build_ready=report.can_generate_build_plan and not unresolved_requirements,
            policy_version=dependency_catalog.policy_version if dependency_catalog else "",
            is_preliminary=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
