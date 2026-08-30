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
                    cpu_data = cpu_data[0]
                cpu_name = normalize_dmi_string(cpu_data.get("Name", "Intel Core Processor"))
                cores = int(cpu_data.get("NumberOfCores", 4))
                threads = int(cpu_data.get("NumberOfLogicalProcessors", 8))
                gen = "Kaby Lake Refresh" if "8" in cpu_name and "U" in cpu_name else "Kaby Lake"
                cpu = CpuInfo(
                    model_name=cpu_name,
                    vendor="GenuineIntel",
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
                    ven_id = ven_match.group(1).lower() if ven_match else "8086"
                    dev_id = dev_match.group(1).lower() if dev_match else "5917"
                    pci = PciDevice(vendor_id=ven_id, device_id=dev_id, device_name=v_name)

                    if ven_id == "8086":
                        igpu = GpuInfo(name=v_name or "Intel UHD Graphics 620", pci=pci, is_igpu=True)
                    elif ven_id == "10de":
                        dgpus.append(GpuInfo(name=v_name or "Nvidia GeForce MX150", pci=pci, is_dgpu=True))
            except Exception as e:
                logger.debug(f"Error parsing Win32_VideoController: {e}")

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
            raw_evidence={"os": "windows"},
        )
