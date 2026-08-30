"""Unit tests for domain model instantiation and serialization."""

import json
from macloader.domain.compatibility import (
    CompatibilityReport,
    CompatibilityState,
    ComponentCompatibilityResult,
    SupportDecision,
)
from macloader.domain.hardware import (
    AudioInfo,
    CpuInfo,
    DisplayInfo,
    GpuInfo,
    HardwareSnapshot,
    InputDeviceInfo,
    NetworkInfo,
    PciDevice,
    StorageInfo,
    ThunderboltInfo,
    UsbDevice,
)
from macloader.domain.build_plan import BuildPlan


def test_pci_device_serialization() -> None:
    pci = PciDevice(
        vendor_id="8086",
        device_id="5917",
        subsystem_vendor_id="17aa",
        subsystem_device_id="225c",
        pci_slot="0000:00:02.0",
        device_class="0300",
        vendor_name="Intel",
        device_name="UHD 620",
    )
    assert pci.canonical_id == "8086:5917"
    pci_dict = pci.to_dict()
    assert pci_dict["vendor_id"] == "8086"
    assert pci_dict["device_id"] == "5917"

    rebuilt = PciDevice.from_dict(pci_dict)
    assert rebuilt.canonical_id == "8086:5917"
    assert rebuilt.subsystem_vendor_id == "17aa"


def test_hardware_snapshot_roundtrip() -> None:
    snapshot = HardwareSnapshot(
        manufacturer="LENOVO",
        product_name="20L7CTO1WW",
        product_version="ThinkPad T480s",
        machine_type="20L7",
        bios_version="1.53",
        cpu=CpuInfo(model_name="Intel i7-8550U", vendor="GenuineIntel", cores=4, threads=8),
        igpu=GpuInfo(
            name="Intel UHD Graphics 620",
            pci=PciDevice(vendor_id="8086", device_id="5917"),
            is_igpu=True,
        ),
        ethernet=[NetworkInfo(name="Intel I219-LM", kind="ethernet", pci=PciDevice(vendor_id="8086", device_id="15d7"))],
        storage=[StorageInfo(model="WD Black NVMe", kind="nvme")],
    )

    json_str = snapshot.to_json()
    data = json.loads(json_str)
    assert data["manufacturer"] == "LENOVO"
    assert data["machine_type"] == "20L7"
    assert data["cpu"]["cores"] == 4

    rebuilt = HardwareSnapshot.from_dict(data)
    assert rebuilt.manufacturer == "LENOVO"
    assert rebuilt.cpu is not None
    assert rebuilt.cpu.cores == 4
    assert rebuilt.igpu is not None
    assert rebuilt.igpu.pci.canonical_id == "8086:5917"


def test_compatibility_report_serialization() -> None:
    decision = SupportDecision(
        target="model:thinkpad-t480s",
        state=CompatibilityState.EXPERIMENTAL,
        reason="Model supported in v0.1",
        evidence="Machine type 20L7",
    )
    result = ComponentCompatibilityResult(
        category="graphics",
        component_id="intel-uhd-620",
        component_name="Intel UHD Graphics 620",
        decision=decision,
    )
    report = CompatibilityReport(
        snapshot_id="test-snap-1",
        target_macos="sequoia",
        model_id="thinkpad-t480s",
        model_name="Lenovo ThinkPad T480s",
        overall_state=CompatibilityState.EXPERIMENTAL,
        model_decision=decision,
        component_results=[result],
        warnings=["Test warning"],
        can_generate_build_plan=True,
    )

    json_str = report.to_json()
    data = json.loads(json_str)
    assert data["target_macos"] == "sequoia"
    assert data["overall_state"] == "EXPERIMENTAL"

    rebuilt = CompatibilityReport.from_dict(data)
    assert rebuilt.target_macos == "sequoia"
    assert rebuilt.overall_state == CompatibilityState.EXPERIMENTAL
    assert len(rebuilt.component_results) == 1
    assert rebuilt.can_generate_build_plan is True


def test_build_plan_serialization() -> None:
    plan = BuildPlan(
        target_model="Lenovo ThinkPad T480s",
        target_macos="tahoe",
        hardware_snapshot_id="test-snap-1",
        support_state=CompatibilityState.EXPERIMENTAL,
        required_capabilities=["accelerated_intel_uhd_620", "alc257_audio"],
        planned_components=[{"name": "Intel UHD 620", "policy": "WhateverGreen"}],
        unresolved_requirements=["Audio layout-id selection deferred"],
        warnings=["Tahoe wireless stack note"],
    )
    json_str = plan.to_json()
    data = json.loads(json_str)
    assert data["target_macos"] == "tahoe"
    assert "accelerated_intel_uhd_620" in data["required_capabilities"]
    assert data["is_preliminary"] is True

    rebuilt = BuildPlan.from_dict(data)
    assert rebuilt.target_macos == "tahoe"
    assert rebuilt.required_capabilities == ["accelerated_intel_uhd_620", "alc257_audio"]
