"""Panel detection uses preferred timings and never claims touch absence."""
import json
from pathlib import Path

from macloader.detection.display import edid_panel
from macloader.detection.linux import LinuxHardwareProvider
from macloader.detection.windows import WindowsHardwareProvider
from macloader.domain.hardware import InputDeviceInfo


def edid() -> bytes:
    data = bytearray(128)
    data[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    data[54:56] = b"\x01\x01"
    data[56], data[58] = 128, 112  # 1920
    data[59], data[61] = 56, 64  # 1080
    data[72:90] = b"\x00\x00\x00\xfc\x00TEST-PANEL\n  "
    data[127] = (-sum(data[:127])) % 256
    return bytes(data)


def test_edid_valid_invalid_and_serial_omission() -> None:
    assert edid_panel(edid()) == ("TEST-PANEL", "1920x1080")
    assert edid_panel(b"bad") is None
    invalid = bytearray(edid())
    invalid[10] ^= 1
    assert edid_panel(bytes(invalid)) is None


def test_linux_internal_panel_detection(tmp_path: Path) -> None:
    connector = tmp_path / "class" / "drm" / "card0-eDP-1"
    connector.mkdir(parents=True)
    (connector / "status").write_text("connected")
    (connector / "edid").write_bytes(edid())
    provider = LinuxHardwareProvider(sys_root=str(tmp_path))
    panels = provider.probe_displays([])
    assert panels[0].resolution == "1920x1080"
    assert panels[0].touch_capability is None
    touch = provider.probe_displays([InputDeviceInfo("touch", "i2c", "touchscreen")])
    assert touch[0].touch_capability is True
    (connector / "edid").write_bytes(b"invalid")
    assert provider.probe_displays([])[0].resolution is None


def test_windows_preferred_internal_monitor_and_codec() -> None:
    def runner(script: str) -> str:
        if "WmiMonitorConnectionParams" in script:
            return json.dumps([{"InstanceName": "panel-private-id", "VideoOutputTechnology": 11, "Active": True}])
        if "WmiMonitorListedSupportedSourceModes" in script:
            return json.dumps([{"InstanceName": "panel-private-id", "PreferredMonitorSourceModeIndex": 0,
                                "MonitorSourceModes": [{"HorizontalActivePixels": 1920, "VerticalActivePixels": 1080}]}])
        if "Win32_PnPEntity" in script:
            return json.dumps([{"Name": "Realtek Audio", "Class": "MEDIA", "PNPDeviceID": "HDAUDIO\\FUNC_01&VEN_10EC&DEV_0257&SUBSYS_17AA2258"}])
        return "[]"
    observed = WindowsHardwareProvider(runner).probe()
    assert observed.displays[0].resolution == "1920x1080"
    assert observed.displays[0].touch_capability is None
    assert "panel-private-id" not in observed.to_json()
    assert observed.audio[0].codec_vendor_id == "10ec"
    assert observed.audio[0].codec_subsystem_id == "17aa:2258"
