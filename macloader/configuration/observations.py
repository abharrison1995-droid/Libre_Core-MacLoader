"""Sanitized observed facts and conservative, scoped evidence reconciliation."""
from dataclasses import dataclass, replace
import json
from typing import Iterable

from macloader.build.acpi import normalize_bios_binding
from macloader.detection.sanitize import sanitize_hardware_snapshot
from macloader.domain.configuration import HardwareConfirmation, HardwareObservation, ObservationStatus, UserConfiguration
from macloader.domain.contracts import canonical_json_digest
from macloader.domain.evidence import EvidenceCompleteness
from macloader.domain.hardware import HardwareSnapshot


EVIDENCE_SCOPES = {
    "acpi": ("machine", "bios", "cpu", "graphics", "input", "thunderbolt", "usb_controllers"),
    "usb": ("machine", "bios", "usb_controllers"),
}


def hardware_facts(snapshot: HardwareSnapshot) -> dict[str, object]:
    safe = sanitize_hardware_snapshot(snapshot)
    internal = [display for display in safe.displays if display.is_internal]
    inventory = safe.get_inventory_status()
    return {
        "machine.manufacturer": safe.manufacturer or None,
        "machine.product": safe.product_name or None,
        "machine.type": safe.machine_type,
        "bios.version": normalize_bios_binding(safe.bios_version or "") or None,
        "cpu.identity": safe.cpu.to_dict() if safe.cpu else None,
        "graphics.igpu": safe.igpu.pci.canonical_id if safe.igpu else None,
        "graphics.dgpus": [gpu.pci.canonical_id for gpu in safe.dgpus] if safe.dgpus or inventory.get("video") or inventory.get("pci") else None,
        "audio.codec": sorted({f"{a.codec_vendor_id}:{a.codec_device_id}" for a in safe.audio if a.codec_vendor_id and a.codec_device_id}) or None,
        "audio.subsystem": sorted({a.codec_subsystem_id for a in safe.audio if a.codec_subsystem_id}) or None,
        "wifi.identity": sorted({device.pci.canonical_id for device in safe.wifi if device.pci}) or None,
        "ethernet.identity": sorted({device.pci.canonical_id for device in safe.ethernet if device.pci}) or None,
        "bluetooth.identity": sorted({device.usb.canonical_id for device in safe.bluetooth if device.usb}) or None,
        "storage.identity": [dict(model=s.model, kind=s.kind, pci=s.pci.to_dict() if s.pci else None) for s in safe.storage] or None,
        "input.topology": [device.to_dict() for device in safe.input_devices] or None,
        "thunderbolt.identity": safe.thunderbolt.to_dict() if safe.thunderbolt and safe.thunderbolt.present else None,
        "usb_controllers.identity": [device.to_dict() for device in safe.usb_controllers] or None,
        "panel.resolution": sorted({d.resolution for d in internal if d.resolution}) or None,
        "panel.touch": sorted({d.touch_capability for d in internal if d.touch_capability is not None}) or None,
        "panel.model": sorted({d.name for d in internal if d.name}) or None,
    }


def scope_digest(snapshot: HardwareSnapshot, scopes: tuple[str, ...]) -> str:
    facts = hardware_facts(snapshot)
    return canonical_json_digest({key: value for key, value in facts.items() if key.split(".")[0] in scopes})


def snapshot_observations(snapshot: HardwareSnapshot) -> tuple[HardwareObservation, ...]:
    facts = hardware_facts(snapshot)
    digest = canonical_json_digest(facts)
    source = str(snapshot.raw_evidence.get("os") or "unknown-provider")
    return tuple(HardwareObservation(
        field_path=key, value=json.dumps(value, sort_keys=True, separators=(",", ":")) if value is not None else "",
        source=source, provider_version="2", evidence_ref=None,
        status=ObservationStatus.OBSERVED if value is not None else ObservationStatus.UNKNOWN,
        confidence="high" if value is not None else "unknown",
        observed_at=snapshot.timestamp, snapshot_digest=digest,
        bios_binding=normalize_bios_binding(snapshot.bios_version or ""),
    ) for key, value in sorted(facts.items()))


@dataclass(frozen=True)
class ObservationReconciliation:
    observations: tuple[HardwareObservation, ...]
    confirmations: tuple[HardwareConfirmation, ...]
    unresolved: tuple[str, ...]


def reconcile_observations(observations: Iterable[HardwareObservation]) -> ObservationReconciliation:
    groups: dict[str, list[HardwareObservation]] = {}
    for observation in observations:
        groups.setdefault(observation.field_path, []).append(observation)
    reconciled: list[HardwareObservation] = []
    confirmations: list[HardwareConfirmation] = []
    unresolved: list[str] = []
    for field, values in sorted(groups.items()):
        present = {o.value for o in values if o.status == ObservationStatus.OBSERVED and o.value}
        conflicts = len(present) > 1 or any(o.status == ObservationStatus.CONFLICTING for o in values)
        high = [o for o in values if o.status == ObservationStatus.OBSERVED and o.confidence == "high" and o.value]
        if conflicts:
            reconciled.extend(replace(o, status=ObservationStatus.CONFLICTING) for o in values)
            unresolved.append(field)
        elif high:
            reconciled.extend(values)
            confirmations.append(HardwareConfirmation(field, "auto-accepted", high[0].value, "Consistent high-confidence provider evidence"))
        else:
            reconciled.extend(values)
            unresolved.append(field)
    return ObservationReconciliation(tuple(reconciled), tuple(confirmations), tuple(unresolved))


def reconcile_configuration(draft: UserConfiguration, snapshot: HardwareSnapshot) -> UserConfiguration:
    observations = snapshot_observations(snapshot)
    # Keep the original observation timestamp for unchanged facts, so a
    # repeat launch does not mutate policy acknowledgement bindings.
    previous = {o.field_path: o for o in draft.observations}
    observations = tuple(
        previous[o.field_path] if o.field_path in previous and replace(
            o, observed_at=previous[o.field_path].observed_at,
            snapshot_digest=previous[o.field_path].snapshot_digest,
        ) == previous[o.field_path] else o
        for o in observations
    )
    result = reconcile_observations(observations)
    changed = {o.field_path for o in observations if previous.get(o.field_path) is None or previous[o.field_path].value != o.value}
    evidence = []
    for record in draft.evidence:
        stale = record.machine_snapshot_id != snapshot.snapshot_id
        if record.input_scope:
            stale = stale or record.input_digest != scope_digest(snapshot, record.input_scope)
        elif changed:
            stale = True  # Older metadata has no proven invalidation scope.
        evidence.append(replace(record, completeness=EvidenceCompleteness.STALE) if stale else record)
    data = snapshot.to_dict()
    data.pop("snapshot_id", None)
    data.pop("timestamp", None)
    return replace(
        draft, hardware_snapshot_id=snapshot.snapshot_id,
        hardware_snapshot_digest=canonical_json_digest(data),
        observations=result.observations, confirmations=result.confirmations + tuple(
            c for c in draft.confirmations if c.action == "human-confirmed" and c.field_path == "panel.touch"
            and c.input_scope == ("machine", "bios", "panel") and c.input_digest == scope_digest(snapshot, c.input_scope)
            and hardware_facts(snapshot)[c.field_path] is None),
        evidence=tuple(evidence), acknowledgements=() if changed else draft.acknowledgements,
        recovery=None if changed else draft.recovery,
    )
