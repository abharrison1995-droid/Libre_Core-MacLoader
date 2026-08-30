"""Hardware domain models representing normalized hardware components and snapshots."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class PciDevice:
    """Represents a PCI device with normalized identifiers."""
    vendor_id: str  # 4-character lowercase hex, e.g. "8086"
    device_id: str  # 4-character lowercase hex, e.g. "5917"
    subsystem_vendor_id: Optional[str] = None
    subsystem_device_id: Optional[str] = None
    pci_slot: Optional[str] = None  # e.g. "0000:00:02.0"
    device_class: Optional[str] = None  # e.g. "0300" (VGA compatible controller)
    vendor_name: Optional[str] = None
    device_name: Optional[str] = None
    driver: Optional[str] = None

    @property
    def canonical_id(self) -> str:
        """Returns standard vendor:device ID string."""
        return f"{self.vendor_id}:{self.device_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "device_id": self.device_id,
            "subsystem_vendor_id": self.subsystem_vendor_id,
            "subsystem_device_id": self.subsystem_device_id,
            "pci_slot": self.pci_slot,
            "device_class": self.device_class,
            "vendor_name": self.vendor_name,
            "device_name": self.device_name,
            "driver": self.driver,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PciDevice":
        return cls(
            vendor_id=data["vendor_id"],
            device_id=data["device_id"],
            subsystem_vendor_id=data.get("subsystem_vendor_id"),
            subsystem_device_id=data.get("subsystem_device_id"),
            pci_slot=data.get("pci_slot"),
            device_class=data.get("device_class"),
            vendor_name=data.get("vendor_name"),
            device_name=data.get("device_name"),
            driver=data.get("driver"),
        )


@dataclass
class UsbDevice:
    """Represents a USB device with normalized identifiers."""
    vendor_id: str  # 4-character lowercase hex
    product_id: str  # 4-character lowercase hex
    bus: Optional[str] = None
    device_num: Optional[str] = None
    vendor_name: Optional[str] = None
    product_name: Optional[str] = None

    @property
    def canonical_id(self) -> str:
        return f"{self.vendor_id}:{self.product_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "bus": self.bus,
            "device_num": self.device_num,
            "vendor_name": self.vendor_name,
            "product_name": self.product_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UsbDevice":
        return cls(
            vendor_id=data["vendor_id"],
            product_id=data["product_id"],
            bus=data.get("bus"),
            device_num=data.get("device_num"),
            vendor_name=data.get("vendor_name"),
            product_name=data.get("product_name"),
        )


@dataclass
class CpuInfo:
    """Processor information."""
    model_name: str
    vendor: str = "GenuineIntel"
    family: Optional[str] = None
    model: Optional[str] = None
    stepping: Optional[str] = None
    cores: int = 4
    threads: int = 8
    generation: Optional[str] = None  # e.g. "Kaby Lake Refresh"
    microcode: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CpuInfo":
        return cls(**data)


@dataclass
class GpuInfo:
    """Graphics processing unit details."""
    name: str
    pci: PciDevice
    is_igpu: bool = False
    is_dgpu: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "pci": self.pci.to_dict(),
            "is_igpu": self.is_igpu,
            "is_dgpu": self.is_dgpu,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GpuInfo":
        return cls(
            name=data["name"],
            pci=PciDevice.from_dict(data["pci"]),
            is_igpu=data.get("is_igpu", False),
            is_dgpu=data.get("is_dgpu", False),
        )


@dataclass
class AudioInfo:
    """Audio controller or codec details."""
    name: str
    codec_name: Optional[str] = None
    codec_vendor_id: Optional[str] = None
    codec_device_id: Optional[str] = None
    pci: Optional[PciDevice] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "codec_name": self.codec_name,
            "codec_vendor_id": self.codec_vendor_id,
            "codec_device_id": self.codec_device_id,
            "pci": self.pci.to_dict() if self.pci else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioInfo":
        pci_data = data.get("pci")
        return cls(
            name=data["name"],
            codec_name=data.get("codec_name"),
            codec_vendor_id=data.get("codec_vendor_id"),
            codec_device_id=data.get("codec_device_id"),
            pci=PciDevice.from_dict(pci_data) if pci_data else None,
        )


@dataclass
class NetworkInfo:
    """Network adapter details (Ethernet, Wi-Fi, Bluetooth, WWAN)."""
    name: str
    kind: str  # "ethernet", "wifi", "bluetooth", "wwan"
    pci: Optional[PciDevice] = None
    usb: Optional[UsbDevice] = None
    mac_address: Optional[str] = None  # sanitized in fixtures

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "pci": self.pci.to_dict() if self.pci else None,
            "usb": self.usb.to_dict() if self.usb else None,
            "mac_address": self.mac_address,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetworkInfo":
        pci_data = data.get("pci")
        usb_data = data.get("usb")
        return cls(
            name=data["name"],
            kind=data["kind"],
            pci=PciDevice.from_dict(pci_data) if pci_data else None,
            usb=UsbDevice.from_dict(usb_data) if usb_data else None,
            mac_address=data.get("mac_address"),
        )


@dataclass
class StorageInfo:
    """Storage device information."""
    model: str
    kind: str  # "nvme", "sata"
    size_bytes: Optional[int] = None
    serial: Optional[str] = None  # sanitized in fixtures
    pci: Optional[PciDevice] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "serial": self.serial,
            "pci": self.pci.to_dict() if self.pci else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StorageInfo":
        pci_data = data.get("pci")
        return cls(
            model=data["model"],
            kind=data["kind"],
            size_bytes=data.get("size_bytes"),
            serial=data.get("serial"),
            pci=PciDevice.from_dict(pci_data) if pci_data else None,
        )


@dataclass
class InputDeviceInfo:
    """Input device details (Touchscreen, Trackpad, TrackPoint, Keyboard)."""
    name: str
    bus: str  # "i2c", "ps2", "smbus", "usb"
    kind: str  # "touchscreen", "trackpad", "trackpoint", "keyboard"
    vendor_id: Optional[str] = None
    product_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InputDeviceInfo":
        return cls(**data)


@dataclass
class DisplayInfo:
    """Display information."""
    name: Optional[str] = None
    resolution: Optional[str] = None
    is_internal: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisplayInfo":
        return cls(**data)


@dataclass
class ThunderboltInfo:
    """Thunderbolt controller information."""
    present: bool = False
    controller_name: Optional[str] = None
    pci: Optional[PciDevice] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "present": self.present,
            "controller_name": self.controller_name,
            "pci": self.pci.to_dict() if self.pci else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ThunderboltInfo":
        pci_data = data.get("pci")
        return cls(
            present=data.get("present", False),
            controller_name=data.get("controller_name"),
            pci=PciDevice.from_dict(pci_data) if pci_data else None,
        )


@dataclass
class HardwareSnapshot:
    """Complete, normalized snapshot of host hardware evidence."""
    manufacturer: str  # e.g. "LENOVO"
    product_name: str  # e.g. "20L7CTO1WW", "ThinkPad T480s"
    product_version: str  # e.g. "ThinkPad T480s"
    machine_type: Optional[str] = None  # e.g. "20L7", "20L8", "20L5", "20L6"
    bios_version: Optional[str] = None
    bios_date: Optional[str] = None
    serial_number: Optional[str] = None  # sanitized
    uuid: Optional[str] = None  # sanitized
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    cpu: Optional[CpuInfo] = None
    igpu: Optional[GpuInfo] = None
    dgpus: List[GpuInfo] = field(default_factory=list)
    audio: List[AudioInfo] = field(default_factory=list)
    ethernet: List[NetworkInfo] = field(default_factory=list)
    wifi: List[NetworkInfo] = field(default_factory=list)
    bluetooth: List[NetworkInfo] = field(default_factory=list)
    storage: List[StorageInfo] = field(default_factory=list)
    usb_controllers: List[PciDevice] = field(default_factory=list)
    usb_devices: List[UsbDevice] = field(default_factory=list)
    thunderbolt: Optional[ThunderboltInfo] = None
    input_devices: List[InputDeviceInfo] = field(default_factory=list)
    displays: List[DisplayInfo] = field(default_factory=list)
    raw_evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "manufacturer": self.manufacturer,
            "product_name": self.product_name,
            "product_version": self.product_version,
            "machine_type": self.machine_type,
            "bios_version": self.bios_version,
            "bios_date": self.bios_date,
            "serial_number": self.serial_number,
            "uuid": self.uuid,
            "cpu": self.cpu.to_dict() if self.cpu else None,
            "igpu": self.igpu.to_dict() if self.igpu else None,
            "dgpus": [g.to_dict() for g in self.dgpus],
            "audio": [a.to_dict() for a in self.audio],
            "ethernet": [e.to_dict() for e in self.ethernet],
            "wifi": [w.to_dict() for w in self.wifi],
            "bluetooth": [b.to_dict() for b in self.bluetooth],
            "storage": [s.to_dict() for s in self.storage],
            "usb_controllers": [u.to_dict() for u in self.usb_controllers],
            "usb_devices": [ud.to_dict() for ud in self.usb_devices],
            "thunderbolt": self.thunderbolt.to_dict() if self.thunderbolt else None,
            "input_devices": [i.to_dict() for i in self.input_devices],
            "displays": [d.to_dict() for d in self.displays],
            "raw_evidence": self.raw_evidence,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HardwareSnapshot":
        cpu_data = data.get("cpu")
        igpu_data = data.get("igpu")
        tb_data = data.get("thunderbolt")

        return cls(
            snapshot_id=data.get("snapshot_id", str(uuid.uuid4())),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            manufacturer=data.get("manufacturer", ""),
            product_name=data.get("product_name", ""),
            product_version=data.get("product_version", ""),
            machine_type=data.get("machine_type"),
            bios_version=data.get("bios_version"),
            bios_date=data.get("bios_date"),
            serial_number=data.get("serial_number"),
            uuid=data.get("uuid"),
            cpu=CpuInfo.from_dict(cpu_data) if cpu_data else None,
            igpu=GpuInfo.from_dict(igpu_data) if igpu_data else None,
            dgpus=[GpuInfo.from_dict(g) for g in data.get("dgpus", [])],
            audio=[AudioInfo.from_dict(a) for a in data.get("audio", [])],
            ethernet=[NetworkInfo.from_dict(e) for e in data.get("ethernet", [])],
            wifi=[NetworkInfo.from_dict(w) for w in data.get("wifi", [])],
            bluetooth=[NetworkInfo.from_dict(b) for b in data.get("bluetooth", [])],
            storage=[StorageInfo.from_dict(s) for s in data.get("storage", [])],
            usb_controllers=[PciDevice.from_dict(u) for u in data.get("usb_controllers", [])],
            usb_devices=[UsbDevice.from_dict(ud) for ud in data.get("usb_devices", [])],
            thunderbolt=ThunderboltInfo.from_dict(tb_data) if tb_data else None,
            input_devices=[InputDeviceInfo.from_dict(i) for i in data.get("input_devices", [])],
            displays=[DisplayInfo.from_dict(d) for d in data.get("displays", [])],
            raw_evidence=data.get("raw_evidence", {}),
        )
