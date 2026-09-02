"""Unit tests for WindowsHardwareProvider using mock PowerShell command runner."""

import json
from macloader.detection.windows import WindowsHardwareProvider


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
    # Must not raise IndexError on an empty JSON array; falls back to defaults instead.
    assert snapshot.cpu is not None
    assert snapshot.cpu.cores == 4
    assert snapshot.cpu.threads == 8

    def mock_runner_null_cores(script: str) -> str:
        if "Win32_ComputerSystem" in script:
            return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
        elif "Win32_Processor" in script:
            return json.dumps({"Name": "Intel(R) Core(TM) i7-8550U CPU", "NumberOfCores": None, "NumberOfLogicalProcessors": None})
        return ""

    provider = WindowsHardwareProvider(command_runner=mock_runner_null_cores)
    snapshot = provider.probe()
    assert snapshot.cpu is not None
    assert snapshot.cpu.cores == 4
    assert snapshot.cpu.threads == 8
