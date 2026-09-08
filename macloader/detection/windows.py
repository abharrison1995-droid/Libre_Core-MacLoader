"""Windows hardware detection provider using CIM / WMI / PowerShell queries."""

import json
import logging
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional

from macloader.detection.base import BaseHardwareProvider
from macloader.detection.normalize import (
    extract_machine_type,
    normalize_dmi_string,
    normalize_hex_id,
)
from macloader.domain.hardware import (
    AudioInfo,
    CpuInfo,
    GpuInfo,
    HardwareSnapshot,
    InputDeviceInfo,
    NetworkInfo,
    PciDevice,
    StorageInfo,
    ThunderboltInfo,
    UsbDevice,
)
from macloader.exceptions import HardwareDetectionError

logger = logging.getLogger(__name__)


class WindowsHardwareProvider(BaseHardwareProvider):
    """Probes Windows system information using PowerShell CIM commands."""

    def __init__(self, command_runner: Optional[Callable[[str], Optional[str]]] = None):
        self.command_runner = command_runner

    def _run_ps(self, script: str, timeout: int = 15) -> Optional[str]:
        """Execute a PowerShell command returning stdout."""
        if self.command_runner:
            return self.command_runner(script)

        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
            logger.debug(f"PowerShell error ({res.returncode}): {res.stderr}")
        except FileNotFoundError:
            logger.debug("PowerShell binary not found")
        except subprocess.TimeoutExpired:
            logger.warning(f"PowerShell command timed out after {timeout}s")
        except Exception as e:
            logger.debug(f"Unexpected error running PowerShell: {e}")
        return None

    def _json_rows(self, script: str) -> List[Dict[str, Any]]:
        return self._parse_json_rows(self._run_ps(script))

    @staticmethod
    def _parse_json_rows(raw: Optional[str]) -> List[Dict[str, Any]]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if isinstance(value, dict):
            return [value]
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    @staticmethod
    def _pnp_ids(pnp_id: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        ven = re.search(r"(?:VEN|VID)_([0-9a-fA-F]{4})", pnp_id or "")
        dev = re.search(r"(?:DEV|PID)_([0-9a-fA-F]{4})", pnp_id or "")
        subsys = re.search(r"SUBSYS_([0-9a-fA-F]{8})", pnp_id or "")
        return (
            ven.group(1).lower() if ven else None,
            dev.group(1).lower() if dev else None,
            subsys.group(1)[4:].lower() if subsys else None,
            subsys.group(1)[:4].lower() if subsys else None,
            pnp_id or None,
        )

    def probe(self) -> HardwareSnapshot:
        """Probe Windows hardware returning a HardwareSnapshot."""
        # 1. Computer System (Manufacturer, Model)
        cs_json = self._run_ps("Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model | ConvertTo-Json")
        mfg = "Unknown"
        model = "Unknown"
        if cs_json:
            try:
                cs = json.loads(cs_json)
                mfg = normalize_dmi_string(cs.get("Manufacturer"))
                model = normalize_dmi_string(cs.get("Model"))
            except Exception as e:
                logger.debug(f"Error parsing Win32_ComputerSystem: {e}")

        # 2. BIOS info
        bios_json = self._run_ps("Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion,ReleaseDate,SerialNumber | ConvertTo-Json")
        bios_ver = None
        bios_date = None
        serial = None
        if bios_json:
            try:
                bios = json.loads(bios_json)
                bios_ver = normalize_dmi_string(bios.get("SMBIOSBIOSVersion")) or None
                bios_date = normalize_dmi_string(bios.get("ReleaseDate")) or None
                serial = normalize_dmi_string(bios.get("SerialNumber")) or None
            except Exception as e:
                logger.debug(f"Error parsing Win32_BIOS: {e}")

        # 3. Processor
        cpu_json = self._run_ps("Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json")
        cpu = None
        if cpu_json:
            try:
                cpu_data = json.loads(cpu_json)
                if isinstance(cpu_data, list):
                    cpu_data = cpu_data[0] if cpu_data else {}
                cpu_name = normalize_dmi_string(cpu_data.get("Name"))
                if not cpu_name:
                    raise ValueError("CPU name is missing")
                cores = int(cpu_data.get("NumberOfCores") or 0)
                threads = int(cpu_data.get("NumberOfLogicalProcessors") or 0)
                gen = "Kaby Lake Refresh" if any(token in cpu_name for token in ("8250U", "8350U", "8550U", "8650U")) else None
                cpu = CpuInfo(
                    model_name=cpu_name,
                    vendor="GenuineIntel" if "intel" in cpu_name.lower() else "Unknown",
                    cores=cores,
                    threads=threads,
                    generation=gen,
                )
            except Exception as e:
                logger.debug(f"Error parsing Win32_Processor: {e}")

        # 4. PnP Video / Graphics
        video_json = self._run_ps("Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID | ConvertTo-Json")
        igpu = None
        dgpus: List[GpuInfo] = []
        if video_json:
            try:
                v_data = json.loads(video_json)
                if isinstance(v_data, dict):
                    v_data = [v_data]
                for v in v_data:
                    v_name = v.get("Name", "")
                    pnp_id = v.get("PNPDeviceID", "")
                    # Extract VEN_xxxx&DEV_xxxx
                    ven_match = re.search(r"VEN_([0-9a-fA-F]{4})", pnp_id)
                    dev_match = re.search(r"DEV_([0-9a-fA-F]{4})", pnp_id)
                    if not ven_match or not dev_match:
                        logger.debug(f"Could not parse PNPDeviceID for video controller: {pnp_id}")
                        continue
                    ven_id = ven_match.group(1).lower()
                    dev_id = dev_match.group(1).lower()
                    subsys_match = re.search(r"SUBSYS_([0-9a-fA-F]{8})", pnp_id)
                    pci = PciDevice(
                        vendor_id=ven_id, device_id=dev_id, device_name=v_name,
                        subsystem_device_id=subsys_match.group(1)[:4].lower() if subsys_match else None,
                        subsystem_vendor_id=subsys_match.group(1)[4:].lower() if subsys_match else None,
                    )

                    if ven_id == "8086":
                        igpu = GpuInfo(name=v_name or "Intel UHD Graphics 620", pci=pci, is_igpu=True)
                    elif ven_id == "10de":
                        dgpus.append(GpuInfo(name=v_name or "Nvidia GeForce MX150", pci=pci, is_dgpu=True))
            except Exception as e:
                logger.debug(f"Error parsing Win32_VideoController: {e}")

        ethernet: List[NetworkInfo] = []
        wifi: List[NetworkInfo] = []
        bluetooth: List[NetworkInfo] = []
        audio: List[AudioInfo] = []
        input_devices: List[InputDeviceInfo] = []
        usb_devices: List[UsbDevice] = []
        usb_controllers: List[PciDevice] = []
        pnp_raw = self._run_ps(
            "Get-CimInstance Win32_PnPEntity | Select-Object Name,PNPDeviceID,Class,Service | ConvertTo-Json"
        )
        pnp_rows = self._parse_json_rows(pnp_raw)
        for row in pnp_rows:
            name = normalize_dmi_string(row.get("Name")) or "Unknown device"
            pnp = str(row.get("PNPDeviceID") or "")
            dev_class = normalize_dmi_string(row.get("Class")) or ""
            ven, dev, subven, subdev, _ = self._pnp_ids(pnp)
            pnp_pci = PciDevice(vendor_id=ven, device_id=dev, subsystem_vendor_id=subven, subsystem_device_id=subdev, device_name=name) if ven and dev and "USB" not in pnp.upper() else None
            usb = UsbDevice(vendor_id=ven, product_id=dev, product_name=name) if ven and dev and "USB" in pnp.upper() else None
            lower = f"{name} {dev_class}".lower()
            if usb:
                usb_devices.append(usb)
            if "bluetooth" in lower:
                bluetooth.append(NetworkInfo(name=name, kind="bluetooth", pci=pnp_pci, usb=usb))
            elif dev_class.lower() == "net" or any(token in lower for token in ("ethernet", "wireless", "wi-fi", "wifi")):
                if any(token in lower for token in ("wi-fi", "wifi", "wireless", "wlan")):
                    wifi.append(NetworkInfo(name=name, kind="wifi", pci=pnp_pci))
                else:
                    ethernet.append(NetworkInfo(name=name, kind="ethernet", pci=pnp_pci))
            elif dev_class.lower() in {"media", "sound"} or "audio" in lower:
                audio.append(AudioInfo(name=name, pci=pnp_pci))
            elif any(token in lower for token in ("keyboard", "trackpoint", "touchpad", "touchscreen", "mouse")):
                kind = "keyboard" if "keyboard" in lower else "trackpoint" if "trackpoint" in lower else "touchscreen" if "touchscreen" in lower else "trackpad"
                input_devices.append(InputDeviceInfo(name=name, bus="usb" if usb else "unknown", kind=kind, vendor_id=ven, product_id=dev))
            elif dev_class.lower() in {"usb", "usbdevice"} and pnp_pci:
                usb_controllers.append(pnp_pci)

        storage: List[StorageInfo] = []
        disk_raw = self._run_ps(
            "Get-CimInstance Win32_DiskDrive | Select-Object Model,InterfaceType,Size,SerialNumber,PNPDeviceID | ConvertTo-Json"
        )
        for row in self._parse_json_rows(disk_raw):
            model_name = normalize_dmi_string(row.get("Model")) or "Unknown storage device"
            interface = normalize_dmi_string(row.get("InterfaceType")) or ""
            storage_text = f"{model_name} {interface}".lower()
            kind = "nvme" if any(token in storage_text for token in ("nvme", "pm981", "mzvlb")) else "sata" if interface.upper() in {"SATA", "ATA"} else "unknown"
            try:
                raw_size = row.get("Size")
                size = int(str(raw_size)) if raw_size else None
            except (TypeError, ValueError):
                size = None
            ven, dev, subven, subdev, _ = self._pnp_ids(str(row.get("PNPDeviceID") or ""))
            disk_pci = PciDevice(vendor_id=ven, device_id=dev, subsystem_vendor_id=subven, subsystem_device_id=subdev) if ven and dev else None
            storage.append(StorageInfo(model=model_name, kind=kind, size_bytes=size, serial=normalize_dmi_string(row.get("SerialNumber")) or None, pci=disk_pci))

        machine_type = extract_machine_type(model, model)

        return HardwareSnapshot(
            manufacturer=mfg,
            product_name=model,
            product_version=model,
            machine_type=machine_type,
            bios_version=bios_ver,
            bios_date=bios_date,
            serial_number=serial,
            cpu=cpu,
            igpu=igpu,
            dgpus=dgpus,
            audio=audio,
            ethernet=ethernet,
            wifi=wifi,
            bluetooth=bluetooth,
            storage=storage,
            usb_controllers=usb_controllers,
            usb_devices=usb_devices,
            input_devices=input_devices,
            raw_evidence={
                "os": "windows",
                "inventory_sources": ["CIM:ComputerSystem", "CIM:BIOS", "CIM:Processor", "CIM:VideoController", "CIM:PnPEntity", "CIM:DiskDrive"],
                "inventory_status": {
                    "computer": bool(cs_json), "bios": bool(bios_json), "cpu": bool(cpu_json),
                    "video": bool(video_json), "pnp": bool(pnp_raw),
                    # Keys consumed by CompatibilityEngine's completeness check —
                    # must match its category names exactly (see engine.py). All
                    # five derive from the single PnP query, so completeness
                    # tracks whether that query itself succeeded, not whether it
                    # happened to enumerate a device in this category.
                    "audio": bool(pnp_raw),
                    "ethernet": bool(pnp_raw),
                    "wifi": bool(pnp_raw),
                    "bluetooth": bool(pnp_raw),
                    "input": bool(pnp_raw),
                    "storage": bool(disk_raw),
                },
            },
        )
