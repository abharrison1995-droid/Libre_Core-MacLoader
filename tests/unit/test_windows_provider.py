"""Unit tests for WindowsHardwareProvider using mock PowerShell command runner."""

import json
from typing import Callable, Optional

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
    assert not any("display audio" in device.name.lower() for device in snapshot.audio)
    assert len(snapshot.audio) == 1
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


# Software network / Bluetooth endpoints that Windows lists in Win32_PnPEntity
# without any PCI or USB identity. Instance suffixes are invented test values.
_SOFTWARE_NETWORK_ROWS = [
    {"Name": "WAN Miniport (IP)", "PNPDeviceID": r"SWD\MSRRAS\MS_NDISWANIP", "Class": "Net"},
    {"Name": "Microsoft Wi-Fi Direct Virtual Adapter", "PNPDeviceID": r"{5D624F94-TEST}\VWIFIMP_WFD\0&TEST&0001", "Class": "Net"},
    {"Name": "Bluetooth Device (RFCOMM Protocol TDI)", "PNPDeviceID": r"BTH\MS_RFCOMM\7&TEST&0&0", "Class": "Bluetooth"},
    {"Name": "Bluetooth Device (Personal Area Network)", "PNPDeviceID": r"BTH\MS_BTHPAN\7&TEST&0&2", "Class": "Net"},
]

_HARDWARE_NETWORK_ROWS = [
    {"Name": "Intel(R) Ethernet Connection (4) I219-V", "PNPDeviceID": r"PCI\VEN_8086&DEV_15D7&SUBSYS_225C17AA&REV_21\3&TEST&0&FE", "Class": "Net"},
    {"Name": "Intel(R) Dual Band Wireless-AC 8265", "PNPDeviceID": r"PCI\VEN_8086&DEV_24FD&SUBSYS_00108086&REV_78\4&TEST&0&00E4", "Class": "Net"},
    {"Name": "Intel(R) Wireless Bluetooth(R)", "PNPDeviceID": r"USB\VID_8087&PID_0A2B\5&TEST&0&7", "Class": "Bluetooth"},
]


def _runner_with_pnp_rows(pnp_rows: list[dict[str, str]]) -> Callable[[str], Optional[str]]:
    def runner(script: str) -> Optional[str]:
        if "Win32_PnPEntity" in script:
            return json.dumps(pnp_rows)
        return _valid_baseline_windows_runner(script)

    return runner


def test_software_network_and_bluetooth_rows_without_hardware_ids_are_ignored() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows(_SOFTWARE_NETWORK_ROWS)).probe()

    assert snapshot.ethernet == []
    assert snapshot.wifi == []
    assert snapshot.bluetooth == []
    # The PnP query itself succeeded, so the categories are confirmed empty, not unknown.
    inventory = snapshot.raw_evidence["inventory_status"]
    assert inventory["ethernet"] is True
    assert inventory["wifi"] is True
    assert inventory["bluetooth"] is True


def test_real_network_and_bluetooth_hardware_is_kept_next_to_software_rows() -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_pnp_rows(_SOFTWARE_NETWORK_ROWS + _HARDWARE_NETWORK_ROWS)
    ).probe()

    assert [eth.pci.canonical_id for eth in snapshot.ethernet if eth.pci] == ["8086:15d7"]
    assert len(snapshot.ethernet) == 1
    assert [wifi.pci.canonical_id for wifi in snapshot.wifi if wifi.pci] == ["8086:24fd"]
    assert len(snapshot.wifi) == 1
    assert [bt.usb.canonical_id for bt in snapshot.bluetooth if bt.usb] == ["8087:0a2b"]
    assert len(snapshot.bluetooth) == 1


def test_software_network_rows_do_not_reach_engine_as_unknown_components(db: Database) -> None:
    pnp_rows = (
        _SOFTWARE_NETWORK_ROWS
        + _HARDWARE_NETWORK_ROWS
        + [
            {"Name": "Realtek High Definition Audio", "PNPDeviceID": r"PCI\VEN_10EC&DEV_0257&SUBSYS_225C17AA", "Class": "MEDIA"},
            {"Name": "Standard PS/2 Keyboard", "PNPDeviceID": r"ACPI\PNP0303\4&TEST&0", "Class": "Keyboard"},
            {"Name": "Synaptics Pointing Device", "PNPDeviceID": r"ACPI\SYN1014\4&TEST&0", "Class": "Mouse"},
        ]
    )
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows(pnp_rows)).probe()

    report = CompatibilityEngine(db=db).evaluate(snapshot, target_macos="sequoia")

    network_results = [r for r in report.component_results if r.category in {"ethernet", "wifi", "bluetooth"}]
    assert {r.category for r in network_results} == {"ethernet", "wifi", "bluetooth"}
    assert len(network_results) == 3
    unknown = [r.component_id for r in report.component_results if r.component_id.startswith("unknown-")]
    assert unknown == []
    assert report.can_generate_build_plan is True


# WWAN modems are unsupported. Vendor/product IDs and instance suffixes are invented.
_FIBOCOM_WWAN_ROW = {"Name": "Fibocom L830-EB-00 LTE WWAN Module", "PNPDeviceID": r"USB\VID_FFF1&PID_0A01&MI_00\6&TEST&0&0000", "Class": "Net"}
_SIERRA_WWAN_ROW = {"Name": "Sierra Wireless Snapdragon X7 LTE-A", "PNPDeviceID": r"USB\VID_FFF2&PID_0B02&MI_08\6&TEST&0&0008", "Class": "Net"}


def test_wwan_usb_net_row_is_not_reported_as_network_hardware() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows([_FIBOCOM_WWAN_ROW])).probe()

    assert snapshot.ethernet == []
    assert snapshot.wifi == []
    assert snapshot.bluetooth == []
    assert "fff1:0a01" in [device.canonical_id for device in snapshot.usb_devices]


def test_sierra_wireless_wwan_row_is_not_classified_as_wifi() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows([_SIERRA_WWAN_ROW])).probe()

    assert snapshot.wifi == []
    assert snapshot.ethernet == []
    assert "fff2:0b02" in [device.canonical_id for device in snapshot.usb_devices]


def test_filter_row_with_pci_identity_is_not_treated_as_wwan() -> None:
    # "Filter" contains the letters "lte"; the word boundary keeps it out of WWAN.
    filter_row = {"Name": "Intel(R) Ethernet Connection I219-V-WFP LightWeight Filter", "PNPDeviceID": r"PCI\VEN_8086&DEV_15D7&SUBSYS_FFFF17AA\3&TEST&0&FF", "Class": "Net"}
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows([filter_row])).probe()

    assert [eth.pci.canonical_id for eth in snapshot.ethernet if eth.pci] == ["8086:15d7"]
    assert snapshot.wifi == []


def test_wwan_row_does_not_reach_engine_as_unknown_component(db: Database) -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_pnp_rows(_HARDWARE_NETWORK_ROWS + [_FIBOCOM_WWAN_ROW])
    ).probe()

    assert len(snapshot.ethernet) == 1
    assert len(snapshot.wifi) == 1
    assert len(snapshot.bluetooth) == 1
    report = CompatibilityEngine(db=db).evaluate(snapshot, target_macos="sequoia")

    unknown = [r.component_id for r in report.component_results if r.component_id.startswith("unknown-")]
    assert unknown == []


# HDAUDIO rows. Instance suffixes are invented test values.
_DISPLAY_AUDIO_ROW = {"Name": "Intel(R) Display Audio", "PNPDeviceID": r"HDAUDIO\FUNC_01&VEN_8086&DEV_280B&SUBSYS_80860101&REV_1000\4&TEST&0&0201", "Class": "MEDIA", "Service": "IntcDAud"}
_ALC257_ROW = {"Name": "Realtek High Definition Audio", "PNPDeviceID": r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0257&SUBSYS_17AA2258&REV_1000\4&TEST&0&0001", "Class": "MEDIA", "Service": "HDAudAddService"}


def test_intel_display_audio_row_yields_no_audio_entry() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows([_DISPLAY_AUDIO_ROW])).probe()

    assert snapshot.audio == []
    # The PnP query succeeded, so audio is confirmed empty rather than unknown.
    assert snapshot.raw_evidence["inventory_status"]["audio"] is True


def test_alc257_codec_row_is_unchanged_next_to_display_audio() -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_pnp_rows([_DISPLAY_AUDIO_ROW, _ALC257_ROW])
    ).probe()

    assert len(snapshot.audio) == 1
    codec = snapshot.audio[0]
    assert codec.codec_vendor_id == "10ec"
    assert codec.codec_device_id == "0257"
    assert f"{codec.codec_vendor_id}:{codec.codec_device_id}" == "10ec:0257"
    assert codec.codec_subsystem_id == "17aa:2258"
    assert codec.pci is None


def test_display_audio_does_not_reach_engine_as_unknown_audio(db: Database) -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_pnp_rows([_DISPLAY_AUDIO_ROW, _ALC257_ROW])
    ).probe()

    report = CompatibilityEngine(db=db).evaluate(snapshot, target_macos="sequoia")

    audio_results = [r for r in report.component_results if r.category == "audio"]
    assert [r.component_id for r in audio_results] == ["realtek-alc257"]
    assert not any(r.component_id == "unknown-audio" for r in report.component_results)


# Win32_DiskDrive rows. Instance suffixes and serial numbers are invented test values.
_SSSTC_NVME_DISK = {
    "Model": "SSSTC CA5-8D256-HP",
    "InterfaceType": "SCSI",
    "Size": "256052966400",
    "SerialNumber": "TEST-SERIAL-0001",
    "PNPDeviceID": r"SCSI\DISK&VEN_NVME&PROD_SSSTC_CA5-8D256\5&TEST&0&000000",
}
_PM981_NVME_DISK = {
    "Model": "SAMSUNG MZVLB256HAHQ-000L7",
    "InterfaceType": "SCSI",
    "Size": "256052966400",
    "SerialNumber": "TEST-SERIAL-0002",
    "PNPDeviceID": r"SCSI\DISK&VEN_NVME&PROD_SAMSUNG_MZVLB256\5&TEST&0&000000",
}
_SATA_IDE_DISK = {
    "Model": "TEST SATA SSD 512GB",
    "InterfaceType": "IDE",
    "Size": "512105932800",
    "SerialNumber": "TEST-SERIAL-0003",
    "PNPDeviceID": r"SCSI\DISK&VEN_TEST&PROD_SATA_SSD\4&TEST&0&000100",
}
_USBSTOR_DISK = {
    "Model": "SanDisk Ultra USB 3.0 USB Device",
    "InterfaceType": "SCSI",
    "Size": "30752636928",
    "SerialNumber": "TEST-SERIAL-0004",
    "PNPDeviceID": r"USBSTOR\DISK&VEN_SANDISK&PROD_ULTRA&REV_1.00\TEST0001&0",
}
_USB_INTERFACE_DISK = {
    "Model": "Test External NVMe Enclosure",
    "InterfaceType": "usb",
    "Size": "1000204886016",
    "SerialNumber": "TEST-SERIAL-0005",
    "PNPDeviceID": r"SCSI\DISK&VEN_NVME&PROD_TEST_ENCLOSURE\7&TEST&0&000000",
}


def _runner_with_disk_rows(disk_rows: list[dict[str, str]]) -> Callable[[str], Optional[str]]:
    def runner(script: str) -> Optional[str]:
        if "Win32_DiskDrive" in script:
            return json.dumps(disk_rows)
        return _valid_baseline_windows_runner(script)

    return runner


def test_scsi_interface_disk_with_ven_nvme_pnp_id_is_nvme() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_disk_rows([_SSSTC_NVME_DISK])).probe()

    assert [(disk.model, disk.kind) for disk in snapshot.storage] == [("SSSTC CA5-8D256-HP", "nvme")]


def test_usbstor_disk_is_absent_from_storage() -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_disk_rows([_SSSTC_NVME_DISK, _USBSTOR_DISK])
    ).probe()

    assert [disk.model for disk in snapshot.storage] == ["SSSTC CA5-8D256-HP"]
    # Skipping a removable disk must not mark the storage query incomplete.
    assert snapshot.raw_evidence["inventory_status"]["storage"] is True


def test_usb_interface_disk_is_absent_from_storage() -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_disk_rows([_SSSTC_NVME_DISK, _USB_INTERFACE_DISK])
    ).probe()

    assert [disk.model for disk in snapshot.storage] == ["SSSTC CA5-8D256-HP"]
    assert snapshot.raw_evidence["inventory_status"]["storage"] is True


def test_only_usb_disks_leave_storage_confirmed_empty() -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_disk_rows([_USBSTOR_DISK, _USB_INTERFACE_DISK])
    ).probe()

    assert snapshot.storage == []
    assert snapshot.raw_evidence["inventory_status"]["storage"] is True


def test_ide_interface_sata_disk_is_classified_as_sata() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_disk_rows([_SATA_IDE_DISK])).probe()

    assert [disk.kind for disk in snapshot.storage] == ["sata"]


def test_pm981_model_is_still_nvme() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_disk_rows([_PM981_NVME_DISK])).probe()

    assert [disk.kind for disk in snapshot.storage] == ["nvme"]


def test_ssstc_nvme_disk_reaches_engine_as_known_storage_and_usb_stick_is_ignored(db: Database) -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_disk_rows([_SSSTC_NVME_DISK, _USBSTOR_DISK, _USB_INTERFACE_DISK])
    ).probe()

    report = CompatibilityEngine(db=db).evaluate(snapshot, target_macos="sequoia")
    storage_results = [r for r in report.component_results if r.category == "storage"]

    assert [(r.component_id, r.component_name) for r in storage_results] == [("standard-nvme", "SSSTC CA5-8D256-HP")]
    assert not any(r.component_id == "unknown-storage" for r in report.component_results)


# Input rows. Vendor/product IDs and instance suffixes are invented test values.
_TOUCHSCREEN_ROW = {"Name": "HID-compliant touch screen", "PNPDeviceID": r"HID\VID_04F3&PID_FFF1&COL01\7&TEST&0&0000", "Class": "HIDClass", "Service": "HidUsb"}
_TRACKPOINT_ROW = {"Name": "ThinkPad TrackPoint", "PNPDeviceID": r"ACPI\LEN0000\4&TEST&0", "Class": "Mouse"}
_PS2_TRACKPOINT_ROW = {"Name": "Lenovo PS/2 Track-Point Mouse", "PNPDeviceID": r"ACPI\LEN0001\4&TEST&0", "Class": "Mouse"}
_KEYBOARD_TRACKPOINT_ROW = {"Name": "ThinkPad Keyboard with TrackPoint", "PNPDeviceID": r"ACPI\LEN0002\4&TEST&0", "Class": "Keyboard"}
_HID_MOUSE_ROW = {"Name": "HID-compliant mouse", "PNPDeviceID": r"HID\VID_FFF3&PID_0C01\7&TEST&0&0000", "Class": "Mouse", "Service": "mouhid"}
_PS2_KEYBOARD_ROW = {"Name": "Standard PS/2 Keyboard", "PNPDeviceID": r"ACPI\PNP0303\4&TEST&0", "Class": "Keyboard"}
_SYNAPTICS_POINTING_ROW = {"Name": "Synaptics Pointing Device", "PNPDeviceID": r"ACPI\SYN0000\4&TEST&0", "Class": "Mouse"}
_ELAN_POINTING_ROW = {"Name": "ELAN Pointing Device", "PNPDeviceID": r"ACPI\ELAN0000\4&TEST&0", "Class": "Mouse"}
_SYNAPTICS_FINGERPRINT_ROW = {"Name": "Synaptics FS7600 Touch Fingerprint Sensor", "PNPDeviceID": r"USB\VID_06CB&PID_FFF4\TEST0001", "Class": "Biometric"}


def _input_kinds(pnp_rows: list[dict[str, str]]) -> list[str]:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows(pnp_rows)).probe()
    return [device.kind for device in snapshot.input_devices]


def test_hid_touch_screen_row_is_touchscreen_and_sets_touch_capability() -> None:
    def runner(script: str) -> Optional[str]:
        if "Win32_PnPEntity" in script:
            return json.dumps([_TOUCHSCREEN_ROW])
        if "WmiMonitorConnectionParams" in script:
            return json.dumps([{"InstanceName": "DISPLAY-TEST", "VideoOutputTechnology": 11, "Active": True}])
        return _valid_baseline_windows_runner(script)

    snapshot = WindowsHardwareProvider(command_runner=runner).probe()

    assert [device.kind for device in snapshot.input_devices] == ["touchscreen"]
    assert len(snapshot.displays) == 1
    assert snapshot.displays[0].touch_capability is True


def test_trackpoint_rows_are_trackpoint_even_when_name_says_keyboard_or_mouse() -> None:
    assert _input_kinds([_TRACKPOINT_ROW]) == ["trackpoint"]
    assert _input_kinds([_PS2_TRACKPOINT_ROW]) == ["trackpoint"]
    assert _input_kinds([_KEYBOARD_TRACKPOINT_ROW]) == ["trackpoint"]


def test_hid_compliant_mouse_alone_does_not_create_a_trackpad() -> None:
    snapshot = WindowsHardwareProvider(command_runner=_runner_with_pnp_rows([_HID_MOUSE_ROW])).probe()

    assert not any(device.kind == "trackpad" for device in snapshot.input_devices)
    assert snapshot.input_devices == []


def test_synaptics_and_elan_pointing_devices_are_trackpads() -> None:
    assert _input_kinds([_SYNAPTICS_POINTING_ROW]) == ["trackpad"]
    assert _input_kinds([_ELAN_POINTING_ROW]) == ["trackpad"]


def test_synaptics_fingerprint_reader_is_not_an_input_device() -> None:
    assert _input_kinds([_SYNAPTICS_FINGERPRINT_ROW]) == []


def test_keyboard_and_trackpoint_reach_engine_as_ps2_trackpad_trackpoint(db: Database) -> None:
    snapshot = WindowsHardwareProvider(
        command_runner=_runner_with_pnp_rows([_PS2_KEYBOARD_ROW, _TRACKPOINT_ROW, _HID_MOUSE_ROW])
    ).probe()
    assert sorted(device.kind for device in snapshot.input_devices) == ["keyboard", "trackpoint"]

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    input_results = [r for r in report.component_results if r.category == "input"]
    assert [r.component_id for r in input_results] == ["thinkpad-trackpad-trackpoint"] * 2
    assert not any(r.component_id.startswith("unknown-input") for r in report.component_results)
    plan = engine.generate_build_plan(report)
    assert "ps2_trackpad_trackpoint" in plan.required_capabilities


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
