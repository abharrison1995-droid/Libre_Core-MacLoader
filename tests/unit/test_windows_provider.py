"""Unit tests for WindowsHardwareProvider using mock PowerShell command runner."""

import json
from typing import Optional

from macloader.compatibility.engine import CompatibilityEngine
from macloader.configuration.campaign_match import match_campaign
from macloader.configuration.observations import snapshot_observations
from macloader.database.loader import Database, get_database
from macloader.detection.windows import WindowsHardwareProvider
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.configuration import ObservationStatus


def test_windows_provider_with_mock_runner() -> None:
    def mock_runner(script: str) -> str:
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
        elif "Win32_BIOS" in script:
            return json.dumps({
                "SMBIOSBIOSVersion": "N22ET76W (1.53 )",
                "ReleaseDate": "20230118000000.000000+000",
                "SerialNumber": "PF1ABCDE",
            })
        elif "Win32_Processor" in script:
            return json.dumps({
                "Name": "Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz",
                "NumberOfCores": 4,
                "NumberOfLogicalProcessors": 8,
            })
        elif "Win32_VideoController" in script:
            return json.dumps([
                {
                    "Name": "Intel(R) UHD Graphics 620",
                    "PNPDeviceID": r"PCI\VEN_8086&DEV_5917&SUBSYS_225C17AA&REV_07\3&11583659&0&10",
                }
            ])
        return ""

    provider = WindowsHardwareProvider(command_runner=mock_runner)
    snapshot = provider.probe()

    assert snapshot.manufacturer == "LENOVO"
    assert snapshot.product_name == "20L7CTO1WW"
    assert snapshot.machine_type == "20L7"
    assert snapshot.cpu is not None
    assert snapshot.cpu.cores == 4
    assert snapshot.igpu is not None
    assert snapshot.igpu.pci.canonical_id == "8086:5917"


def test_windows_provider_skips_unparseable_pnp_device_id() -> None:
    def mock_runner(script: str) -> str:
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
        elif "Win32_Processor" in script:
            return json.dumps({"Name": "Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz"})
        elif "Win32_VideoController" in script:
            # PNPDeviceID missing VEN_/DEV_ fields entirely (e.g. a virtual adapter).
            return json.dumps([{"Name": "Unknown Display Adapter", "PNPDeviceID": r"ROOT\BasicDisplay\0000"}])
        return ""

    provider = WindowsHardwareProvider(command_runner=mock_runner)
    snapshot = provider.probe()

    # No GPU should be fabricated when the vendor/device IDs can't be parsed —
    # it must NOT silently fall back to Intel UHD 620 (8086:5917).
    assert snapshot.igpu is None
    assert snapshot.dgpus == []


def test_windows_provider_handles_empty_processor_list_and_null_core_counts() -> None:
    def mock_runner(script: str) -> str:
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
        elif "Win32_Processor" in script:
            # PowerShell can return an empty array, or null fields inside an object.
            return json.dumps([])
        return ""

    provider = WindowsHardwareProvider(command_runner=mock_runner)
    snapshot = provider.probe()
    # Missing processor inventory must remain missing rather than fabricate
    # an Intel CPU and topology.
    assert snapshot.cpu is None

    def mock_runner_null_cores(script: str) -> str:
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
        elif "Win32_Processor" in script:
            return json.dumps({"Name": "Intel(R) Core(TM) i7-8550U CPU", "NumberOfCores": None, "NumberOfLogicalProcessors": None})
        return ""

    provider = WindowsHardwareProvider(command_runner=mock_runner_null_cores)
    snapshot = provider.probe()
    assert snapshot.cpu is not None
    assert snapshot.cpu.cores == 0
    assert snapshot.cpu.threads == 0

    def mock_runner_corrupt_cores(script: str) -> str:
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
        elif "Win32_Processor" in script:
            return json.dumps({"Name": "Intel(R) Core(TM) i7-8550U CPU", "NumberOfCores": "N/A", "NumberOfLogicalProcessors": "bad_value"})
        return ""

    provider_corrupt = WindowsHardwareProvider(command_runner=mock_runner_corrupt_cores)
    snapshot_corrupt = provider_corrupt.probe()
    assert snapshot_corrupt.cpu is not None
    assert snapshot_corrupt.cpu.cores == 0
    assert snapshot_corrupt.cpu.threads == 0


def _valid_baseline_windows_runner(script: str) -> str:
    if "Win32_ComputerSystem" in script:
        return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
    elif "Win32_BIOS" in script:
        return json.dumps({
            "SMBIOSBIOSVersion": "N22ET76W (1.53 )",
            "ReleaseDate": "20230118000000.000000+000",
            "SerialNumber": "PF1ABCDE",
        })
    elif "Win32_Processor" in script:
        return json.dumps({
            "Name": "Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz",
            "NumberOfCores": 4,
            "NumberOfLogicalProcessors": 8,
        })
    elif "Win32_VideoController" in script:
        return json.dumps([
            {
                "Name": "Intel(R) UHD Graphics 620",
                "PNPDeviceID": r"PCI\VEN_8086&DEV_5917&SUBSYS_225C17AA&REV_07\3&11583659&0&10",
            }
        ])
    elif "Win32_PnPEntity" in script:
        return json.dumps([
            {"Name": "Intel(R) Ethernet Connection (4) I219-V", "PNPDeviceID": r"PCI\VEN_8086&DEV_15D8&SUBSYS_225C17AA", "Class": "Net"},
            {"Name": "Intel(R) Dual Band Wireless-AC 8265", "PNPDeviceID": r"PCI\VEN_8086&DEV_24FD&SUBSYS_00108086", "Class": "Net"},
            {"Name": "Realtek High Definition Audio", "PNPDeviceID": r"PCI\VEN_10EC&DEV_0257&SUBSYS_225C17AA", "Class": "MEDIA"},
            {"Name": "Intel(R) Wireless Bluetooth(R)", "PNPDeviceID": r"USB\VID_8087&PID_0A2B", "Class": "Bluetooth"},
            {"Name": "Standard PS/2 Keyboard", "PNPDeviceID": r"ACPI\PNP0303\4&1B2D3D64&0", "Class": "Keyboard"},
            {"Name": "Synaptics Pointing Device", "PNPDeviceID": r"ACPI\SYN1014\4&1B2D3D64&0", "Class": "Mouse"},
        ])
    elif "Win32_DiskDrive" in script:
        return json.dumps([
            {
                "Model": "Samsung SSD 970 EVO Plus 500GB",
                "InterfaceType": "NVMe",
                "Size": "500107862016",
                "SerialNumber": "S4EVNX0M123456",
                "PNPDeviceID": r"SCSI\DISK&VEN_NVME&PROD_SAMSUNG_SSD_970\5&1234567&0&000000",
            }
        ])
    return ""


def test_failed_query_keeps_inventory_unknown_and_denies_build_plan(db: Database) -> None:
    """When a query fails (returns None), inventory remains unconfirmed and build plan is denied."""
    def failing_runner(script: str) -> Optional[str]:
        if "Win32_PnPEntity" in script:
            return None  # Simulated execution failure / timeout
        return _valid_baseline_windows_runner(script)

    provider = WindowsHardwareProvider(command_runner=failing_runner)
    snapshot = provider.probe()

    inv_status = snapshot.raw_evidence["inventory_status"]
    assert inv_status["pnp"] is False
    assert inv_status["audio"] is False
    assert inv_status["wifi"] is False
    assert inv_status["ethernet"] is False

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False
    assert any("unknown" in r.component_id for r in report.component_results)


def test_malformed_json_keeps_inventory_unknown_and_denies_build_plan(db: Database) -> None:
    """When query returns malformed JSON, inventory is not upgraded and build plan is denied."""
    def malformed_runner(script: str) -> Optional[str]:
        if "Win32_PnPEntity" in script:
            return "{ 'unclosed_json_error': True, "  # Malformed JSON
        return _valid_baseline_windows_runner(script)

    provider = WindowsHardwareProvider(command_runner=malformed_runner)
    snapshot = provider.probe()

    inv_status = snapshot.raw_evidence["inventory_status"]
    assert inv_status["audio"] is False
    assert inv_status["wifi"] is False

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False


def test_scalar_output_keeps_inventory_unknown_and_denies_build_plan(db: Database) -> None:
    """Scalars (numbers, strings, booleans) cannot upgrade inventory to complete."""
    for scalar_value in ("42", '"unexpected string output"', "true"):
        def scalar_runner(script: str) -> Optional[str]:
            if "Win32_PnPEntity" in script:
                return scalar_value
            return _valid_baseline_windows_runner(script)

        provider = WindowsHardwareProvider(command_runner=scalar_runner)
        snapshot = provider.probe()

        inv_status = snapshot.raw_evidence["inventory_status"]
        assert inv_status["audio"] is False
        assert inv_status["wifi"] is False

        engine = CompatibilityEngine(db=db)
        report = engine.evaluate(snapshot, target_macos="sequoia")

        assert report.overall_state == CompatibilityState.UNKNOWN
        assert report.can_generate_build_plan is False


def test_mixed_rows_collection_keeps_inventory_unknown_and_denies_build_plan(db: Database) -> None:
    """An array containing non-dictionary elements (mixed rows) cannot upgrade inventory."""
    def mixed_runner(script: str) -> Optional[str]:
        if "Win32_PnPEntity" in script:
            return json.dumps([
                {"Name": "Intel Ethernet", "PNPDeviceID": r"PCI\VEN_8086&DEV_15D8", "Class": "Net"},
                42,  # Invalid non-dict row
                "corrupt_entry",
            ])
        return _valid_baseline_windows_runner(script)

    provider = WindowsHardwareProvider(command_runner=mixed_runner)
    snapshot = provider.probe()

    inv_status = snapshot.raw_evidence["inventory_status"]
    assert inv_status["pnp"] is False
    assert inv_status["audio"] is False

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False


def test_missing_required_fields_keeps_inventory_unknown_and_denies_build_plan(db: Database) -> None:
    """Rows lacking any required identification field cannot upgrade inventory to complete."""
    # Test completely missing fields
    def missing_fields_runner(script: str) -> Optional[str]:
        if "Win32_PnPEntity" in script:
            return json.dumps([{"IrrelevantColumn": "SomeValue"}])  # Missing Name and PNPDeviceID
        return _valid_baseline_windows_runner(script)

    provider = WindowsHardwareProvider(command_runner=missing_fields_runner)
    snapshot = provider.probe()

    inv_status = snapshot.raw_evidence["inventory_status"]
    assert inv_status["audio"] is False
    assert inv_status["ethernet"] is False

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False

    # Test partially missing fields in PnP (Name present, but PNPDeviceID missing)
    def partial_pnp_runner(script: str) -> Optional[str]:
        if "Win32_PnPEntity" in script:
            return json.dumps([{"Name": "Intel Ethernet"}])  # Missing PNPDeviceID
        return _valid_baseline_windows_runner(script)

    provider_pnp = WindowsHardwareProvider(command_runner=partial_pnp_runner)
    snapshot_pnp = provider_pnp.probe()
    assert snapshot_pnp.raw_evidence["inventory_status"]["pnp"] is False
    assert snapshot_pnp.raw_evidence["inventory_status"]["audio"] is False
    report_pnp = engine.evaluate(snapshot_pnp, target_macos="sequoia")
    assert report_pnp.overall_state == CompatibilityState.UNKNOWN
    assert report_pnp.can_generate_build_plan is False

    # Test partially missing fields in ComputerSystem (Manufacturer present, but Model missing)
    def partial_cs_runner(script: str) -> Optional[str]:
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO"})  # Missing Model
        return _valid_baseline_windows_runner(script)

    provider_cs = WindowsHardwareProvider(command_runner=partial_cs_runner)
    snapshot_cs = provider_cs.probe()

    assert snapshot_cs.raw_evidence["inventory_status"]["computer"] is False
    report_cs = engine.evaluate(snapshot_cs, target_macos="sequoia")
    assert report_cs.overall_state in (CompatibilityState.UNKNOWN, CompatibilityState.BLOCKED)
    assert report_cs.can_generate_build_plan is False


def test_empty_pnp_entities_rejected_and_denies_build_plan(db: Database) -> None:
    """A completely empty PnP entity result (empty string or empty list) is invalid and denies build plan."""
    for empty_val in ("", "[]", "[   ]"):
        def empty_pnp_runner(script: str) -> Optional[str]:
            if "Win32_PnPEntity" in script:
                return empty_val
            return _valid_baseline_windows_runner(script)

        provider = WindowsHardwareProvider(command_runner=empty_pnp_runner)
        snapshot = provider.probe()

        inv_status = snapshot.raw_evidence["inventory_status"]
        assert inv_status["pnp"] is False
        assert inv_status["audio"] is False
        assert inv_status["input"] is False

        engine = CompatibilityEngine(db=db)
        report = engine.evaluate(snapshot, target_macos="sequoia")
        assert report.overall_state == CompatibilityState.UNKNOWN
        assert report.can_generate_build_plan is False


def test_confirmed_empty_inventory_is_distinct_from_unknown(db: Database) -> None:
    """Documented empty output ('', '[]', 'null') marks confirmed-empty inventory distinctly and contrasts with unconfirmed."""
    engine = CompatibilityEngine(db=db)

    # 1. Confirmed empty storage is valid and preserves build eligibility
    for empty_output in ("", "[]", "[   ]", "null"):
        def empty_disk_runner(script: str) -> Optional[str]:
            if "Win32_DiskDrive" in script:
                return empty_output
            return _valid_baseline_windows_runner(script)

        provider = WindowsHardwareProvider(command_runner=empty_disk_runner)
        snapshot = provider.probe()

        assert snapshot.storage == []
        inv_status = snapshot.raw_evidence["inventory_status"]
        assert inv_status["storage"] is True

        query_details = snapshot.raw_evidence["query_status"]["storage"]
        assert query_details["execution_success"] is True
        assert query_details["parse_success"] is True
        assert query_details["is_confirmed_empty"] is True
        assert query_details["is_complete"] is True

        report = engine.evaluate(snapshot, target_macos="sequoia")
        assert report.can_generate_build_plan is True

    # 2. Contrast: Failed/unconfirmed storage query denies build plan
    def failed_disk_runner(script: str) -> Optional[str]:
        if "Win32_DiskDrive" in script:
            return None
        return _valid_baseline_windows_runner(script)

    provider_failed = WindowsHardwareProvider(command_runner=failed_disk_runner)
    snapshot_failed = provider_failed.probe()
    assert snapshot_failed.raw_evidence["inventory_status"]["storage"] is False
    report_failed = engine.evaluate(snapshot_failed, target_macos="sequoia")
    assert report_failed.overall_state == CompatibilityState.UNKNOWN
    assert report_failed.can_generate_build_plan is False


def test_complete_valid_windows_hardware_snapshot_is_build_eligible(db: Database) -> None:
    """A valid Windows hardware probe produces a complete inventory and passes build eligibility."""
    provider = WindowsHardwareProvider(command_runner=_valid_baseline_windows_runner)
    snapshot = provider.probe()

    inv_status = snapshot.raw_evidence["inventory_status"]
    assert all(inv_status.values()), f"Expected all inventory categories to be complete: {inv_status}"

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state in (CompatibilityState.SUPPORTED, CompatibilityState.EXPERIMENTAL)
    assert report.can_generate_build_plan is True


def test_pnp_inventory_keeps_rows_with_optional_names_and_ignores_missing_identity() -> None:
    pnp_rows = [
        {"Name": "Intel(R) USB 3.1 eXtensible Host Controller - 1.10 (Microsoft)", "PNPDeviceID": r"PCI\VEN_8086&DEV_15C1&SUBSYS_225817AA", "Class": None, "Service": "USBXHCI"},
        {"Name": "Intel Ethernet Connection I219-V", "PNPDeviceID": r"PCI\VEN_8086&DEV_15D8&SUBSYS_225817AA", "Class": "Net"},
        {"Name": "Intel Dual Band Wireless-AC 8265", "PNPDeviceID": r"PCI\VEN_8086&DEV_24FD&SUBSYS_00108086", "Class": "Net"},
        {"Name": "Realtek Audio", "PNPDeviceID": r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0257&SUBSYS_17AA2258", "Class": "MEDIA"},
        {"Name": "Intel(R) Display Audio", "PNPDeviceID": r"HDAUDIO\FUNC_01&VEN_8086&DEV_280B&SUBSYS_80860101", "Class": None, "Service": "IntcDAud"},
        {"Name": "Intel Wireless Bluetooth", "PNPDeviceID": r"USB\VID_8087&PID_0A2B", "Class": "Bluetooth"},
        {"Name": "Standard PS/2 Keyboard", "PNPDeviceID": r"ACPI\PNP0303", "Class": "Keyboard"},
        {"Name": "ELAN PTP Touchpad", "PNPDeviceID": r"ACPI\ELAN0001", "Class": "Mouse"},
        {"Name": "Intel Thunderbolt Controller", "PNPDeviceID": r"PCI\VEN_8086&DEV_15BF&SUBSYS_225817AA", "Class": "System"},
        {"Name": "Intel(R) USB 3.0 eXtensible Host Controller - 1.0 (Microsoft)", "PNPDeviceID": r"PCI\VEN_8086&DEV_9D2F&SUBSYS_225817AA", "Class": None, "Service": "USBXHCI"},
        {"Name": None, "PNPDeviceID": r"ACPI\UNKNOWN0001"},
        {"Name": "Unidentified PnP entity", "PNPDeviceID": "   ", "Class": "System"},
    ]

    scripts: list[str] = []

    def runner(script: str) -> Optional[str]:
        scripts.append(script)
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO", "Model": "20L8S4P100"})
        if "Win32_BIOS" in script:
            return json.dumps({"SMBIOSBIOSVersion": "N22ET85W (1.62 )"})
        if "Win32_Processor" in script:
            return json.dumps({"Name": "Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz", "NumberOfCores": 4, "NumberOfLogicalProcessors": 8})
        if "Win32_VideoController" in script:
            return json.dumps([{"Name": "Intel UHD Graphics 620", "PNPDeviceID": r"PCI\VEN_8086&DEV_5917&SUBSYS_225817AA"}])
        if "Win32_PnPEntity" in script:
            assert "Where-Object" in script and "PNPDeviceID" in script
            return json.dumps(pnp_rows)
        if "Win32_DiskDrive" in script:
            return json.dumps([{"Model": "SSSTC CA5-8D256-HP", "InterfaceType": "SCSI", "Size": "256052966400", "PNPDeviceID": r"SCSI\DISK&VEN_NVME&PROD_SSSTC"}])
        if "WmiMonitorConnectionParams" in script:
            return json.dumps([{"InstanceName": "DISPLAY-TEST", "VideoOutputTechnology": 11, "Active": True}])
        if "WmiMonitorListedSupportedSourceModes" in script:
            return json.dumps([{"InstanceName": "DISPLAY-TEST", "PreferredMonitorSourceModeIndex": 0, "MonitorSourceModes": [{"HorizontalActivePixels": 1920, "VerticalActivePixels": 1080}]}])
        return ""

    snapshot = WindowsHardwareProvider(command_runner=runner).probe()
    inventory = snapshot.raw_evidence["inventory_status"]
    assert inventory["pnp"] is True
    assert all(inventory[name] for name in ("audio", "ethernet", "wifi", "bluetooth", "input"))

    assert snapshot.audio[0].codec_vendor_id == "10ec"
    assert snapshot.audio[0].codec_device_id == "0257"
    assert snapshot.audio[0].codec_subsystem_id == "17aa:2258"
    display_audio = next(device for device in snapshot.audio if "display audio" in device.name.lower())
    assert display_audio.codec_vendor_id is None
    assert display_audio.codec_device_id is None
    assert display_audio.codec_subsystem_id is None
    ethernet = snapshot.ethernet[0]
    wifi = snapshot.wifi[0]
    bluetooth = snapshot.bluetooth[0]
    assert ethernet.pci is not None and ethernet.pci.canonical_id == "8086:15d8"
    assert wifi.pci is not None and wifi.pci.canonical_id == "8086:24fd"
    assert bluetooth.usb is not None and bluetooth.usb.canonical_id == "8087:0a2b"
    assert {device.kind for device in snapshot.input_devices} == {"keyboard", "trackpad"}
    assert snapshot.thunderbolt is not None and snapshot.thunderbolt.present
    assert {device.canonical_id for device in snapshot.usb_controllers} == {"8086:15c1", "8086:9d2f"}
    assert snapshot.displays[0].resolution == "1920x1080"
    assert snapshot.displays[0].touch_capability is None
    timing_script = next(script for script in scripts if "WmiMonitorListedSupportedSourceModes" in script)
    assert "HorizontalActivePixels = $_.HorizontalActivePixels" in timing_script
    assert "VerticalActivePixels = $_.VerticalActivePixels" in timing_script

    query_status = snapshot.raw_evidence["query_status"]["pnp"]
    assert query_status["is_complete"] is True
    assert query_status["row_count"] == len(pnp_rows) - 1

    observations = {item.field_path: item for item in snapshot_observations(snapshot)}
    for field in ("audio.codec", "audio.subsystem", "ethernet.identity", "wifi.identity", "bluetooth.identity", "input.topology", "usb_controllers.identity"):
        assert observations[field].status == ObservationStatus.OBSERVED

    match = match_campaign(snapshot, get_database())
    assert match.campaign is not None
    assert match.campaign.campaign_id == "t480s-20l8-n22et85w-162-sequoia"
    assert not match.mismatches
    assert match.unknown == ("panel.touch",)


def test_pnp_inventory_rejects_rows_without_any_usable_identity() -> None:
    result = WindowsHardwareProvider._parse_cim_output(
        json.dumps([{"Name": "Unknown device", "PNPDeviceID": None}, {"Name": "Also unknown", "PNPDeviceID": " "}]),
        allow_empty=False,
    )
    filtered = WindowsHardwareProvider._usable_pnp_inventory(result)
    assert filtered.execution_success is True
    assert filtered.parse_success is False
    assert filtered.rows == []
    assert filtered.error_message == "No PnP rows contain a usable PNPDeviceID"


def test_general_cim_parser_remains_strict_for_required_fields() -> None:
    result = WindowsHardwareProvider._parse_cim_output(
        json.dumps({"Manufacturer": "LENOVO", "Model": None}),
        required_fields=["Manufacturer", "Model"],
        allow_empty=False,
    )
    assert result.execution_success is True
    assert result.parse_success is False
    assert result.rows == []
    assert result.error_message == "Row missing required fields: ['Model']"
