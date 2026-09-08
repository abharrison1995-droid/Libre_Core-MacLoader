"""Windows hardware detection provider using CIM / WMI / PowerShell queries."""

from dataclasses import dataclass
import json
import logging
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional

from macloader.detection.base import BaseHardwareProvider
from macloader.detection.normalize import (
    extract_machine_type,
    infer_cpu_generation,
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


@dataclass(frozen=True)
class CimQueryResult:
    """Structured result of a CIM query separating execution, parsing, and empty inventory."""
    execution_success: bool
    parse_success: bool
    is_confirmed_empty: bool
    rows: List[Dict[str, Any]]
    raw: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        """True if execution and schema parsing succeeded, yielding complete inventory."""
        return self.execution_success and self.parse_success

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_success": self.execution_success,
            "parse_success": self.parse_success,
            "is_confirmed_empty": self.is_confirmed_empty,
            "is_complete": self.is_complete,
            "row_count": len(self.rows),
            "error_message": self.error_message,
        }


class WindowsHardwareProvider(BaseHardwareProvider):
    """Probes Windows system information using PowerShell CIM commands."""

    def __init__(self, command_runner: Optional[Callable[[str], Optional[str]]] = None):
        self.command_runner = command_runner

    def _run_ps(self, script: str, timeout: int = 15) -> Optional[str]:
        """Execute a PowerShell command returning stdout."""
        if self.command_runner:
            return self.command_runner(script)

        try:
            cmd = f"$ErrorActionPreference = 'Stop'; {script}"
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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

    @staticmethod
    def _parse_cim_output(
        raw: Optional[str],
        required_fields: Optional[List[str]] = None,
        allow_empty: bool = True,
    ) -> CimQueryResult:
        if raw is None:
            return CimQueryResult(
                execution_success=False,
                parse_success=False,
                is_confirmed_empty=False,
                rows=[],
                raw=None,
                error_message="Query execution failed or timed out",
            )

        stripped = raw.strip()
        if stripped in ("", "$null"):
            if allow_empty:
                return CimQueryResult(
                    execution_success=True,
                    parse_success=True,
                    is_confirmed_empty=True,
                    rows=[],
                    raw=raw,
                )
            return CimQueryResult(
                execution_success=True,
                parse_success=False,
                is_confirmed_empty=False,
                rows=[],
                raw=raw,
                error_message="Empty output is not valid for this query",
            )

        try:
            value = json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            return CimQueryResult(
                execution_success=True,
                parse_success=False,
                is_confirmed_empty=False,
                rows=[],
                raw=raw,
                error_message=f"JSON parse error: {e}",
            )

        if value is None:
            if allow_empty:
                return CimQueryResult(
                    execution_success=True,
                    parse_success=True,
                    is_confirmed_empty=True,
                    rows=[],
                    raw=raw,
                )
            return CimQueryResult(
                execution_success=True,
                parse_success=False,
                is_confirmed_empty=False,
                rows=[],
                raw=raw,
                error_message="Empty output is not valid for this query",
            )

        if not isinstance(value, (dict, list)):
            return CimQueryResult(
                execution_success=True,
                parse_success=False,
                is_confirmed_empty=False,
                rows=[],
                raw=raw,
                error_message=f"Expected JSON object or array, got scalar {type(value).__name__}",
            )

        items = [value] if isinstance(value, dict) else value

        if len(items) == 0:
            if allow_empty:
                return CimQueryResult(
                    execution_success=True,
                    parse_success=True,
                    is_confirmed_empty=True,
                    rows=[],
                    raw=raw,
                )
            return CimQueryResult(
                execution_success=True,
                parse_success=False,
                is_confirmed_empty=False,
                rows=[],
                raw=raw,
                error_message="Empty output is not valid for this query",
            )

        if not all(isinstance(item, dict) for item in items):
            return CimQueryResult(
                execution_success=True,
                parse_success=False,
                is_confirmed_empty=False,
                rows=[],
                raw=raw,
                error_message="Collection contains non-dictionary elements",
            )

        if required_fields:
            for item in items:
                missing = [field for field in required_fields if item.get(field) is None]
                if missing:
                    return CimQueryResult(
                        execution_success=True,
                        parse_success=False,
                        is_confirmed_empty=False,
                        rows=[],
                        raw=raw,
                        error_message=f"Row missing required fields: {missing}",
                    )

        return CimQueryResult(
            execution_success=True,
            parse_success=True,
            is_confirmed_empty=False,
            rows=items,
            raw=raw,
        )

    def _query_cim(
        self,
        script: str,
        required_fields: Optional[List[str]] = None,
        allow_empty: bool = True,
    ) -> CimQueryResult:
        return self._parse_cim_output(
            self._run_ps(script),
            required_fields=required_fields,
            allow_empty=allow_empty,
        )

    def _json_rows(self, script: str) -> List[Dict[str, Any]]:
        return self._query_cim(script).rows

    @classmethod
    def _parse_json_rows(cls, raw: Optional[str]) -> List[Dict[str, Any]]:
        return cls._parse_cim_output(raw).rows

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
        cs_res = self._query_cim(
            "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model | ConvertTo-Json",
            required_fields=["Manufacturer", "Model"],
            allow_empty=False,
        )
        mfg = "Unknown"
        model = "Unknown"
        if cs_res.rows:
            cs = cs_res.rows[0]
            mfg = normalize_dmi_string(cs.get("Manufacturer")) or "Unknown"
            model = normalize_dmi_string(cs.get("Model")) or "Unknown"

        # 2. BIOS info
        bios_res = self._query_cim(
            "Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion,ReleaseDate,SerialNumber | ConvertTo-Json",
            required_fields=["SMBIOSBIOSVersion"],
            allow_empty=False,
        )
        bios_ver = None
        bios_date = None
        serial = None
        if bios_res.rows:
            bios = bios_res.rows[0]
            bios_ver = normalize_dmi_string(bios.get("SMBIOSBIOSVersion")) or None
            bios_date = normalize_dmi_string(bios.get("ReleaseDate")) or None
            serial = normalize_dmi_string(bios.get("SerialNumber")) or None

        # 3. Processor
        cpu_res = self._query_cim(
            "Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json",
            required_fields=["Name"],
            allow_empty=False,
        )
        cpu = None
        if cpu_res.rows:
            cpu_data = cpu_res.rows[0]
            cpu_name = normalize_dmi_string(cpu_data.get("Name"))
            if cpu_name:
                try:
                    cores = int(cpu_data.get("NumberOfCores") or 0)
                except (TypeError, ValueError):
                    cores = 0
                try:
                    threads = int(cpu_data.get("NumberOfLogicalProcessors") or 0)
                except (TypeError, ValueError):
                    threads = 0
                gen = infer_cpu_generation(cpu_name)
                cpu = CpuInfo(
                    model_name=cpu_name,
                    vendor="GenuineIntel" if "intel" in cpu_name.lower() else "Unknown",
                    cores=cores,
                    threads=threads,
                    generation=gen,
                )

        # 4. PnP Video / Graphics
        video_res = self._query_cim(
            "Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID | ConvertTo-Json",
            required_fields=["Name", "PNPDeviceID"],
            allow_empty=False,
        )
        igpu = None
        dgpus: List[GpuInfo] = []
        if video_res.rows:
            for v in video_res.rows:
                v_name = v.get("Name", "")
                pnp_id = v.get("PNPDeviceID", "")
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

        ethernet: List[NetworkInfo] = []
        wifi: List[NetworkInfo] = []
        bluetooth: List[NetworkInfo] = []
        audio: List[AudioInfo] = []
        input_devices: List[InputDeviceInfo] = []
        usb_devices: List[UsbDevice] = []
        usb_controllers: List[PciDevice] = []

        pnp_res = self._query_cim(
            "Get-CimInstance Win32_PnPEntity | Select-Object Name,PNPDeviceID,Class,Service | ConvertTo-Json",
            required_fields=["Name", "PNPDeviceID"],
            allow_empty=False,
        )
        if pnp_res.rows:
            for row in pnp_res.rows:
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
        disk_res = self._query_cim(
            "Get-CimInstance Win32_DiskDrive | Select-Object Model,InterfaceType,Size,SerialNumber,PNPDeviceID | ConvertTo-Json",
            required_fields=["Model", "PNPDeviceID"],
            allow_empty=True,
        )
        if disk_res.rows:
            for row in disk_res.rows:
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
        pnp_ok = pnp_res.is_complete and not pnp_res.is_confirmed_empty

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
                    "computer": cs_res.is_complete and not cs_res.is_confirmed_empty,
                    "bios": bios_res.is_complete and not bios_res.is_confirmed_empty,
                    "cpu": cpu_res.is_complete and cpu is not None,
                    "video": video_res.is_complete and not video_res.is_confirmed_empty,
                    "pnp": pnp_ok,
                    "audio": pnp_ok,
                    "ethernet": pnp_ok,
                    "wifi": pnp_ok,
                    "bluetooth": pnp_ok,
                    "input": pnp_ok,
                    "storage": disk_res.is_complete,
                },
                "query_status": {
                    "computer": cs_res.to_dict(),
                    "bios": bios_res.to_dict(),
                    "cpu": cpu_res.to_dict(),
                    "video": video_res.to_dict(),
                    "pnp": pnp_res.to_dict(),
                    "storage": disk_res.to_dict(),
                },
            },
        )

