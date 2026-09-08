"""Linux hardware detection provider utilizing sysfs, DMI, /proc, and PCI utilities."""

import logging
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

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
from macloader.exceptions import HardwareDetectionError

logger = logging.getLogger(__name__)


class LinuxHardwareProvider(BaseHardwareProvider):
    """Probes Linux sysfs and system tables to generate a HardwareSnapshot."""

    def __init__(
        self,
        sys_root: str = "/sys",
        proc_root: str = "/proc",
        raise_on_error: bool = False,
    ):
        self.sys_root = Path(sys_root)
        self.proc_root = Path(proc_root)
        self.raise_on_error = raise_on_error
        self.source_errors: Dict[str, List[str]] = {}

    def _record_error(self, category: str, error: str) -> None:
        """Record an enumeration or read error for a specific hardware category."""
        if category not in self.source_errors:
            self.source_errors[category] = []
        self.source_errors[category].append(error)

    def _read_file(self, path: Path, category: Optional[str] = None) -> Optional[str]:
        """Safely read a sysfs or procfs file returning stripped content."""
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()
        except (FileNotFoundError, IsADirectoryError):
            return None
        except PermissionError as e:
            msg = f"Permission denied reading {path}: {e}"
            logger.debug(msg)
            if category:
                self._record_error(category, msg)
            return None
        except (OSError, IOError) as e:
            msg = f"Unable to read file {path}: {e}"
            logger.debug(msg)
            if category:
                self._record_error(category, msg)
            return None

    def _iter_dir_safe(self, directory: Path, category: str) -> List[Path]:
        """Safely list child paths of a directory, capturing permission and OS errors."""
        try:
            if not directory.exists() or not directory.is_dir():
                return []
            return list(directory.iterdir())
        except PermissionError as e:
            msg = f"Permission denied accessing directory {directory}: {e}"
            logger.warning(msg)
            self._record_error(category, msg)
            return []
        except (FileNotFoundError, OSError) as e:
            msg = f"Error accessing directory {directory}: {e}"
            logger.debug(msg)
            self._record_error(category, msg)
            return []

    def _safe_glob(self, directory: Path, pattern: str, category: str) -> List[Path]:
        """Safely glob patterns in a directory, capturing permission and OS errors."""
        try:
            if not directory.exists() or not directory.is_dir():
                return []
            return list(directory.glob(pattern))
        except PermissionError as e:
            msg = f"Permission denied globbing {pattern} in {directory}: {e}"
            logger.warning(msg)
            self._record_error(category, msg)
            return []
        except (FileNotFoundError, OSError) as e:
            msg = f"Error globbing {pattern} in {directory}: {e}"
            logger.debug(msg)
            self._record_error(category, msg)
            return []

    def _run_command(self, cmd: List[str], timeout: int = 5) -> Optional[str]:
        """Safely execute a command without shell interpolation."""
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
            logger.debug(f"Command {' '.join(cmd)} failed with returncode {res.returncode}: {res.stderr}")
        except FileNotFoundError:
            logger.debug(f"Command not found: {cmd[0]}")
        except subprocess.TimeoutExpired:
            logger.warning(f"Command {' '.join(cmd)} timed out after {timeout}s")
        except Exception as e:
            logger.debug(f"Error running {' '.join(cmd)}: {e}")
        return None

    def probe_dmi(self) -> Dict[str, Optional[str]]:
        """Read system DMI properties from /sys/class/dmi/id/."""
        dmi_dir = self.sys_root / "class" / "dmi" / "id"
        vendor = self._read_file(dmi_dir / "sys_vendor", category="dmi")
        prod_name = self._read_file(dmi_dir / "product_name", category="dmi")
        prod_ver = self._read_file(dmi_dir / "product_version", category="dmi")
        bios_ver = self._read_file(dmi_dir / "bios_version", category="dmi")
        bios_date = self._read_file(dmi_dir / "bios_date", category="dmi")
        # On standard Linux, product_serial/uuid are mode 0400 (root only); non-root read failure
        # should not invalidate an otherwise healthy DMI inventory.
        serial = self._read_file(dmi_dir / "product_serial")
        uuid_str = self._read_file(dmi_dir / "product_uuid")

        # Fallback to dmidecode if sysfs DMI is empty/unavailable
        if not vendor and not prod_name:
            dmi_out = self._run_command(["dmidecode", "-t", "system"])
            if dmi_out:
                for line in dmi_out.splitlines():
                    if "Manufacturer:" in line:
                        vendor = line.split(":", 1)[1].strip()
                    elif "Product Name:" in line:
                        prod_name = line.split(":", 1)[1].strip()
                    elif "Version:" in line:
                        prod_ver = line.split(":", 1)[1].strip()
                    elif "Serial Number:" in line:
                        serial = line.split(":", 1)[1].strip()
                    elif "UUID:" in line:
                        uuid_str = line.split(":", 1)[1].strip()

            bios_out = self._run_command(["dmidecode", "-t", "bios"])
            if bios_out:
                for line in bios_out.splitlines():
                    if "Version:" in line and not bios_ver:
                        bios_ver = line.split(":", 1)[1].strip()
                    elif "Release Date:" in line and not bios_date:
                        bios_date = line.split(":", 1)[1].strip()

        return {
            "vendor": normalize_dmi_string(vendor),
            "product_name": normalize_dmi_string(prod_name),
            "product_version": normalize_dmi_string(prod_ver),
            "bios_version": normalize_dmi_string(bios_ver) or None,
            "bios_date": normalize_dmi_string(bios_date) or None,
            "serial": normalize_dmi_string(serial) or None,
            "uuid": normalize_dmi_string(uuid_str) or None,
        }

    def probe_cpu(self) -> Optional[CpuInfo]:
        """Probe CPU details from /proc/cpuinfo."""
        cpuinfo = self._read_file(self.proc_root / "cpuinfo", category="cpu")
        if not cpuinfo:
            return None

        model_name = "Intel Core Processor"
        vendor = "GenuineIntel"
        family = None
        model = None
        stepping = None
        cores_count = 0
        physical_cores = 0
        microcode = None

        for line in cpuinfo.splitlines():
            if ":" not in line:
                continue
            key, val = [x.strip() for x in line.split(":", 1)]
            if key == "model name" and model_name == "Intel Core Processor":
                model_name = val
            elif key == "vendor_id":
                vendor = val
            elif key == "cpu family" and not family:
                family = val
            elif key == "model" and not model:
                model = val
            elif key == "stepping" and not stepping:
                stepping = val
            elif key == "processor":
                cores_count += 1
            elif key == "cpu cores" and not physical_cores:
                try:
                    physical_cores = int(val)
                except ValueError:
                    pass
            elif key == "microcode" and not microcode:
                microcode = val

        # Infer generation if possible
        generation = infer_cpu_generation(model_name)

        return CpuInfo(
            model_name=model_name,
            vendor=vendor,
            family=family,
            model=model,
            stepping=stepping,
            cores=physical_cores,
            threads=max(cores_count, 1),
            generation=generation,
            microcode=microcode,
        )

    def probe_pci_devices(self) -> List[PciDevice]:
        """Probe PCI devices from sysfs or lspci."""
        devices: List[PciDevice] = []
        pci_dir = self.sys_root / "bus" / "pci" / "devices"

        slot_paths = self._iter_dir_safe(pci_dir, category="pci")
        for slot_path in slot_paths:
            try:
                slot = slot_path.name.replace("_", ":") if slot_path.name.count("_") == 2 else slot_path.name
                vendor_raw = self._read_file(slot_path / "vendor", category="pci")
                device_raw = self._read_file(slot_path / "device", category="pci")
                subvendor_raw = self._read_file(slot_path / "subsystem_vendor", category="pci")
                subdevice_raw = self._read_file(slot_path / "subsystem_device", category="pci")
                class_raw = self._read_file(slot_path / "class", category="pci")

                vendor_id = normalize_hex_id(vendor_raw)
                device_id = normalize_hex_id(device_raw)

                if vendor_id and device_id:
                    dev_class = normalize_hex_id(class_raw, length=6) or (normalize_hex_id(class_raw, length=4) if class_raw else None)
                    driver_name = None
                    try:
                        driver_link = slot_path / "driver"
                        if driver_link.is_symlink() or driver_link.exists():
                            driver_name = driver_link.resolve().name
                    except (PermissionError, FileNotFoundError, OSError) as e:
                        logger.debug(f"Unable to resolve driver symlink for {slot_path}: {e}")

                    devices.append(
                        PciDevice(
                            vendor_id=vendor_id,
                            device_id=device_id,
                            subsystem_vendor_id=normalize_hex_id(subvendor_raw),
                            subsystem_device_id=normalize_hex_id(subdevice_raw),
                            pci_slot=slot,
                            device_class=dev_class,
                            driver=driver_name,
                        )
                    )
                else:
                    self._record_error("pci", f"Failed reading PCI slot {slot_path.name}: unreadable or disappearing vendor/device ID")
            except (PermissionError, FileNotFoundError, OSError) as e:
                logger.debug(f"Error reading PCI device at {slot_path}: {e}")
                self._record_error("pci", f"Failed reading PCI slot {slot_path}: {e}")

        # If sysfs PCI was empty, fallback to lspci -mm -nn
        if not devices:
            lspci_out = self._run_command(["lspci", "-mm", "-nn"])
            if lspci_out:
                for line in lspci_out.splitlines():
                    # Format: Slot "Class [ClassID]" "Vendor [VendorID]" "Device [DeviceID]" "SVendor [SVendorID]" "SDevice [SDeviceID]"
                    # Example: 00:02.0 "VGA compatible controller [0300]" "Intel Corporation [8086]" "UHD Graphics 620 [5917]" -r07 "Lenovo [17aa]" "Device [225c]"
                    slot_match = re.match(r"^(\S+)\s+", line)
                    ids = re.findall(r"\[([0-9a-fA-F]{4})\]", line)
                    if slot_match and len(ids) >= 2:
                        slot = slot_match.group(1)
                        # ids might be [class, vendor, device, subvendor, subdevice] or [vendor, device]
                        if len(ids) == 2:
                            vendor_id = ids[0].lower()
                            device_id = ids[1].lower()
                            devices.append(PciDevice(vendor_id=vendor_id, device_id=device_id, pci_slot=slot))
                        elif len(ids) >= 3:
                            # first id is class (e.g. 0300)
                            dev_class = ids[0].lower()
                            vendor_id = ids[1].lower()
                            device_id = ids[2].lower()
                            devices.append(
                                PciDevice(
                                    vendor_id=vendor_id,
                                    device_id=device_id,
                                    subsystem_vendor_id=ids[3].lower() if len(ids) >= 4 else None,
                                    subsystem_device_id=ids[4].lower() if len(ids) >= 5 else None,
                                    pci_slot=slot,
                                    device_class=dev_class,
                                )
                            )

        return devices

    def probe_gpus(self, pci_devices: List[PciDevice]) -> Tuple[Optional[GpuInfo], List[GpuInfo]]:
        """Identify iGPU and dGPUs from PCI devices."""
        igpu: Optional[GpuInfo] = None
        dgpus: List[GpuInfo] = []

        for dev in pci_devices:
            # Intel Display controller (class starts with 03, vendor 8086)
            is_display_class = dev.device_class and dev.device_class.startswith("03")
            is_intel_gpu_id = dev.vendor_id == "8086" and dev.device_id in ("5917", "5916", "3ea0", "3ea5", "5926", "5927", "1916")
            is_nvidia_gpu_id = dev.vendor_id == "10de"
            is_amd_gpu_id = dev.vendor_id == "1002" and is_display_class

            if (dev.vendor_id == "8086" and is_display_class) or is_intel_gpu_id:
                name = "Intel UHD Graphics 620" if dev.device_id in ("5917", "3ea0") else f"Intel display controller ({dev.canonical_id})"
                igpu = GpuInfo(name=name, pci=dev, is_igpu=True, is_dgpu=False)
            elif is_nvidia_gpu_id or is_amd_gpu_id:
                name = "Nvidia GeForce MX150" if dev.device_id in ("1d10", "1d12") else f"Discrete GPU ({dev.vendor_id}:{dev.device_id})"
                dgpus.append(GpuInfo(name=name, pci=dev, is_igpu=False, is_dgpu=True))

        return igpu, dgpus

    def probe_audio(self, pci_devices: List[PciDevice]) -> List[AudioInfo]:
        """Probe audio controllers and codecs."""
        audio_list: List[AudioInfo] = []

        # Check /proc/asound/card*/codec*
        asound_dir = self.proc_root / "asound"
        cards = self._safe_glob(asound_dir, "card*", category="audio")
        for card in cards:
            codecs = self._safe_glob(card, "codec*", category="audio")
            for codec_file in codecs:
                content = self._read_file(codec_file, category="audio")
                if content:
                    codec_name = None
                    codec_id_match = None
                    for line in content.splitlines():
                        if line.startswith("Codec:"):
                            codec_name = line.split(":", 1)[1].strip()
                        elif line.startswith("Address:"):
                            pass
                        elif "Vendor Id:" in line:
                            codec_id_match = re.search(r"0x([0-9a-fA-F]{8})", line)
                    if codec_name:
                        vendor_id = None
                        device_id = None
                        if codec_id_match:
                            hex_full = codec_id_match.group(1).lower()
                            vendor_id = hex_full[:4]
                            device_id = hex_full[4:]
                        audio_list.append(
                            AudioInfo(
                                name=codec_name,
                                codec_name=codec_name,
                                codec_vendor_id=vendor_id,
                                codec_device_id=device_id,
                            )
                        )

        # If codecs were not read, report the controller but do not invent a
        # codec. A typical T480 codec is not evidence from sysfs.
        if not audio_list:
            for dev in pci_devices:
                # Class 0403 is High Definition Audio
                if (dev.device_class and dev.device_class.startswith("0403")) or (dev.vendor_id == "8086" and dev.device_id in ("9d71", "9d70", "a348")):
                    audio_list.append(
                        AudioInfo(
                            name="Intel HD Audio Controller",
                            codec_name=None,
                            codec_vendor_id=None,
                            codec_device_id=None,
                            pci=dev,
                        )
                    )

        return audio_list

    def probe_network(self, pci_devices: List[PciDevice], usb_devices: Optional[List[UsbDevice]] = None) -> Tuple[List[NetworkInfo], List[NetworkInfo], List[NetworkInfo]]:
        """Identify Ethernet, Wi-Fi, and Bluetooth adapters."""
        ethernet: List[NetworkInfo] = []
        wifi: List[NetworkInfo] = []
        bluetooth: List[NetworkInfo] = []

        for dev in pci_devices:
            # Ethernet: Intel I219-LM / I219-V (e.g. 8086:15d7, 8086:15d8, 8086:15b7, 8086:15b8)
            if dev.vendor_id == "8086" and dev.device_id in ("15d7", "15d8", "15b7", "15b8"):
                name = "Intel I219-LM PCI Express Gigabit Ethernet" if dev.device_id in ("15d7", "15b7") else "Intel I219-V Gigabit Ethernet"
                ethernet.append(NetworkInfo(name=name, kind="ethernet", pci=dev))
            # Wi-Fi: Intel Dual Band Wireless-AC 8265 (24fd), 9560 (2526), 9260 (2526), AX200 (2723), AX210 (2725)
            elif dev.vendor_id == "8086" and dev.device_id in ("24fd", "2526", "9526", "2723", "2725", "3165", "095a"):
                names = {
                    "24fd": "Intel Dual Band Wireless-AC 8265",
                    "2526": "Intel Wireless-AC 9260/9560",
                    "2723": "Intel Wi-Fi 6 AX200",
                    "2725": "Intel Wi-Fi 6E AX210",
                }
                wifi.append(NetworkInfo(name=names.get(dev.device_id, "Intel Wireless Adapter"), kind="wifi", pci=dev))
            # Broadcom Wi-Fi
            elif dev.vendor_id == "14e4" and dev.device_id in ("43a0", "43ba", "43a3", "4360", "4352"):
                wifi.append(NetworkInfo(name="Broadcom Wireless Adapter", kind="wifi", pci=dev))

        # Check USB for Bluetooth controllers
        for udev in usb_devices or []:
            if udev.vendor_id == "8087" and udev.product_id in ("0a2b", "0aaa", "0026", "0029", "0032"):
                bluetooth.append(
                    NetworkInfo(
                        name="Intel Bluetooth Wireless Controller",
                        kind="bluetooth",
                        usb=udev,
                    )
                )

        return ethernet, wifi, bluetooth

    def probe_storage(self, pci_devices: List[PciDevice]) -> List[StorageInfo]:
        """Probe NVMe and SATA storage controllers."""
        storage_list: List[StorageInfo] = []
        for dev in pci_devices:
            # Class 0108 is NVMe, 0106 is SATA AHCI
            if (dev.device_class and dev.device_class.startswith("0108")) or dev.vendor_id in ("144d", "15b7", "1c5c", "1987", "c0a9"):
                model_name = "NVMe Storage Controller"
                if dev.vendor_id == "144d" and dev.device_id in ("a808", "a809"):
                    model_name = "Samsung PM981 / PM981a NVMe SSD"
                elif dev.vendor_id == "15b7":
                    model_name = "Western Digital WD Black NVMe SSD"
                storage_list.append(StorageInfo(model=model_name, kind="nvme", pci=dev))
            elif dev.device_class and dev.device_class.startswith("0106"):
                storage_list.append(StorageInfo(model="Intel SATA AHCI Controller", kind="sata", pci=dev))

        # Also inspect /sys/class/block for actual device models if available
        block_dir = self.sys_root / "class" / "block"
        nvme_disks = self._safe_glob(block_dir, "nvme*n1", category="storage")
        for disk in nvme_disks:
            try:
                model = self._read_file(disk / "device" / "model", category="storage")
                if model:
                    # Avoid duplicates
                    normalized_model = model.lower().replace(" ", "")
                    if not any(s.model.lower().replace(" ", "") == normalized_model or ("pm981" in normalized_model and "pm981" in s.model.lower()) for s in storage_list):
                        storage_list.append(StorageInfo(model=model, kind="nvme"))
            except (PermissionError, FileNotFoundError, OSError) as e:
                logger.debug(f"Error inspecting NVMe disk {disk}: {e}")
                self._record_error("storage", f"Failed inspecting NVMe disk {disk}: {e}")

        sd_disks = self._safe_glob(block_dir, "sd*", category="storage")
        for disk in sd_disks:
            try:
                if not disk.name[-1].isdigit():  # Only whole disks, not partitions
                    model = self._read_file(disk / "device" / "model", category="storage")
                    if model and not any(s.model == model for s in storage_list):
                        storage_list.append(StorageInfo(model=model, kind="sata"))
            except (PermissionError, FileNotFoundError, OSError) as e:
                logger.debug(f"Error inspecting block device {disk}: {e}")
                self._record_error("storage", f"Failed inspecting disk {disk}: {e}")

        return storage_list

    def probe_usb_devices(self) -> List[UsbDevice]:
        """Probe USB devices from /sys/bus/usb/devices."""
        devices: List[UsbDevice] = []
        usb_dir = self.sys_root / "bus" / "usb" / "devices"

        dev_paths = self._iter_dir_safe(usb_dir, category="usb")
        for dev_path in dev_paths:
            try:
                id_vendor = self._read_file(dev_path / "idVendor", category="usb")
                id_product = self._read_file(dev_path / "idProduct", category="usb")
                vendor_norm = normalize_hex_id(id_vendor)
                prod_norm = normalize_hex_id(id_product)

                if vendor_norm and prod_norm:
                    mfg = self._read_file(dev_path / "manufacturer", category="usb")
                    prod = self._read_file(dev_path / "product", category="usb")
                    devices.append(
                        UsbDevice(
                            vendor_id=vendor_norm,
                            product_id=prod_norm,
                            bus=dev_path.name,
                            vendor_name=mfg,
                            product_name=prod,
                        )
                    )
                else:
                    self._record_error("usb", f"Failed reading USB device {dev_path.name}: unreadable or disappearing vendor/product ID")
            except (PermissionError, FileNotFoundError, OSError) as e:
                logger.debug(f"Error reading USB device at {dev_path}: {e}")
                self._record_error("usb", f"Failed reading USB device {dev_path}: {e}")

        return devices

    def probe_input_devices(self) -> List[InputDeviceInfo]:
        """Probe touchscreen, trackpad, and TrackPoint input devices."""
        input_list: List[InputDeviceInfo] = []
        bus_input_devices = self._read_file(self.proc_root / "bus" / "input" / "devices", category="input")

        if bus_input_devices:
            for block in bus_input_devices.split("\n\n"):
                if not block.strip():
                    continue
                name_match = re.search(r'N: Name="([^"]+)"', block)
                bus_match = re.search(r"I: Bus=([0-9a-fA-F]{4})", block)
                name = name_match.group(1) if name_match else "Unknown Input Device"
                bus_hex = bus_match.group(1) if bus_match else "0000"

                name_lower = name.lower()
                if "touchscreen" in name_lower or ("elan" in name_lower and "touch" in name_lower):
                    input_list.append(InputDeviceInfo(name=name, bus="i2c", kind="touchscreen"))
                elif "trackpoint" in name_lower or "dualpoint stick" in name_lower:
                    input_list.append(InputDeviceInfo(name=name, bus="ps2", kind="trackpoint"))
                elif "touchpad" in name_lower or "synaptics" in name_lower or "elan" in name_lower:
                    input_list.append(InputDeviceInfo(name=name, bus="smbus", kind="trackpad"))

        return input_list

    def probe_thunderbolt(self, pci_devices: List[PciDevice]) -> ThunderboltInfo:
        """Check for Thunderbolt controller presence."""
        tb_dir = self.sys_root / "bus" / "thunderbolt" / "devices"
        tb_entries = self._iter_dir_safe(tb_dir, category="thunderbolt")
        if tb_entries:
            return ThunderboltInfo(present=True, controller_name="Intel Thunderbolt 3 Controller (Alpine Ridge)")

        for dev in pci_devices:
            if dev.vendor_id == "8086" and dev.device_id in ("15bf", "15d3", "1576", "1578"):
                return ThunderboltInfo(
                    present=True,
                    controller_name="Intel JHL6240 Thunderbolt 3 Controller",
                    pci=dev,
                )

        return ThunderboltInfo(present=False)

    def probe(self) -> HardwareSnapshot:
        """Execute complete hardware probe on Linux."""
        self.source_errors.clear()

        # Check if sys_root / proc_root are accessible at all
        sys_root_accessible = True
        try:
            self.sys_root.stat()
            if not self.sys_root.is_dir():
                sys_root_accessible = False
                self._record_error("sys_root", f"sys_root is not a directory: {self.sys_root}")
        except PermissionError as e:
            self._record_error("sys_root", f"Permission denied accessing sys_root {self.sys_root}: {e}")
            sys_root_accessible = False
        except (FileNotFoundError, OSError) as e:
            self._record_error("sys_root", f"Error accessing sys_root {self.sys_root}: {e}")
            sys_root_accessible = False

        proc_root_accessible = True
        try:
            self.proc_root.stat()
            if not self.proc_root.is_dir():
                proc_root_accessible = False
                self._record_error("proc_root", f"proc_root is not a directory: {self.proc_root}")
        except PermissionError as e:
            self._record_error("proc_root", f"Permission denied accessing proc_root {self.proc_root}: {e}")
            proc_root_accessible = False
        except (FileNotFoundError, OSError) as e:
            self._record_error("proc_root", f"Error accessing proc_root {self.proc_root}: {e}")
            proc_root_accessible = False

        if (not sys_root_accessible or not proc_root_accessible) and self.raise_on_error:
            raise HardwareDetectionError(
                f"Linux hardware detection root directories are inaccessible: sys_root={self.sys_root} (ok={sys_root_accessible}), proc_root={self.proc_root} (ok={proc_root_accessible})"
            )

        dmi = self.probe_dmi()
        cpu = self.probe_cpu()
        pci_devices = self.probe_pci_devices()
        igpu, dgpus = self.probe_gpus(pci_devices)
        audio = self.probe_audio(pci_devices)
        usb_devices = self.probe_usb_devices()
        ethernet, wifi, bluetooth = self.probe_network(pci_devices, usb_devices)
        storage = self.probe_storage(pci_devices)
        input_devices = self.probe_input_devices()
        thunderbolt = self.probe_thunderbolt(pci_devices)

        # Check if raise_on_error was requested
        if self.raise_on_error and self.source_errors:
            total_errs = sum(len(v) for v in self.source_errors.values())
            raise HardwareDetectionError(
                f"Linux hardware detection encountered {total_errs} error(s) during enumeration: {self.source_errors}"
            )

        # Extract machine type from product version/name
        machine_type = extract_machine_type(dmi.get("product_version"), dmi.get("product_name"))

        usb_controllers = [
            dev for dev in pci_devices if dev.device_class and dev.device_class.startswith("0c03")
        ]

        # Calculate category completeness.
        # NEVER convert enumeration failures into confirmed absence!
        dmi_has_err = bool(self.source_errors.get("dmi"))
        dmi_ok = bool(dmi.get("product_name")) and not dmi_has_err

        cpu_has_err = bool(self.source_errors.get("cpu"))
        cpu_ok = (cpu is not None) and not cpu_has_err

        pci_has_err = bool(self.source_errors.get("pci"))
        # PCI enumeration must have succeeded without error AND produced devices.
        # On a real PC, PCI device count cannot be 0.
        pci_ok = (len(pci_devices) > 0) and not pci_has_err

        usb_has_err = bool(self.source_errors.get("usb"))
        usb_dir = self.sys_root / "bus" / "usb" / "devices"
        usb_ok = not usb_has_err and usb_dir.is_dir()

        # Audio completeness:
        # Audio requires PCI audio controller to be reachable without error.
        audio_has_err = bool(self.source_errors.get("audio"))
        audio_ok = pci_ok and not audio_has_err

        # Ethernet completeness:
        # Ethernet is PCI-based on ThinkPads; requires valid PCI enumeration.
        ethernet_ok = pci_ok

        # Wi-Fi completeness:
        # Wi-Fi is PCI-based; requires valid PCI enumeration.
        wifi_ok = pci_ok

        # Bluetooth completeness:
        # If bluetooth found: True.
        # If bluetooth empty: ONLY True if USB was complete.
        bluetooth_ok = (len(bluetooth) > 0) or usb_ok

        # Storage completeness:
        # Storage controllers are PCI-based; requires valid PCI and block enumeration.
        storage_has_err = bool(self.source_errors.get("storage"))
        storage_ok = pci_ok and not storage_has_err

        # Input completeness:
        # A laptop always has input devices; requires at least 1 input device without error.
        input_has_err = bool(self.source_errors.get("input"))
        input_file_exists = (self.proc_root / "bus" / "input" / "devices").is_file()
        input_ok = (len(input_devices) > 0) and not input_has_err and input_file_exists

        raw_evidence: Dict[str, Any] = {
            "dmi": dmi,
            "pci_count": len(pci_devices),
            "usb_count": len(usb_devices),
            "os": "linux",
            "source_errors": dict(self.source_errors),
            "inventory_status": {
                "dmi": dmi_ok,
                "cpu": cpu_ok,
                "pci": pci_ok,
                "usb": usb_ok,
                "audio": audio_ok,
                "ethernet": ethernet_ok,
                "wifi": wifi_ok,
                "bluetooth": bluetooth_ok,
                "storage": storage_ok,
                "input": input_ok,
            },
        }

        return HardwareSnapshot(
            manufacturer=dmi.get("vendor") or "",
            product_name=dmi.get("product_name") or "",
            product_version=dmi.get("product_version") or "",
            machine_type=machine_type,
            bios_version=dmi.get("bios_version"),
            bios_date=dmi.get("bios_date"),
            serial_number=dmi.get("serial"),
            uuid=dmi.get("uuid"),
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
            thunderbolt=thunderbolt,
            input_devices=input_devices,
            raw_evidence=raw_evidence,
        )
