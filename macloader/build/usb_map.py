"""Generate a codeless injector from current physical and literal firmware proof."""
from dataclasses import dataclass
import hashlib
import plistlib
import re
from typing import Any, Mapping

from macloader.evidence.usb import UsbEvidenceSession
from macloader.evidence.usb_capture import canonical_namespace
from macloader.domain.evidence import EvidenceCompleteness
from macloader.exceptions import BuildPlanError


@dataclass(frozen=True)
class GeneratedUsbMap:
    plist: bytes
    digest: str
    evidence_digest: str
    first_install_label: str


def generate_usb_map(evidence: UsbEvidenceSession, policy: Mapping[str, Any], *, snapshot_id: str,
                     bios_binding: str, smbios: str, first_route: str,
                     addresses: Mapping[str, int], controller_slots: set[str]) -> GeneratedUsbMap:
    if evidence.snapshot_id != snapshot_id or evidence.bios_binding != bios_binding:
        raise BuildPlanError('USB map evidence has the wrong machine or BIOS binding')
    status, unresolved = evidence.completeness
    if status not in {EvidenceCompleteness.COMPLETE, EvidenceCompleteness.PARTIAL} or (unresolved and not all('logical USB-C correlation unresolved' in u for u in unresolved)):
        raise BuildPlanError('USB map evidence is incomplete or conflicting')
    selected = [o for o in evidence.observations if o.logical_port not in policy['excluded_routes'] and o.connector_type != 'USB-C']
    # Frozen P4 explicitly keeps Type-C correlation unresolved; no implicit map expansion.
    routes = {o.logical_port for o in selected}
    if not set(policy['minimum_routes']) <= routes:
        raise BuildPlanError('USB map lacks the reviewed minimum route scope')
    for step in policy['steps']:
        if step['connector'] != 'USB-A':
            continue
        expected_super = step['speed'] == 'super'
        if not any(o.physical_label == step['label'] and o.connector_type == 'USB-A' and
                   ((o.logical_port.startswith('SS') and int(o.tested_speed.removesuffix('Mbps')) >= 5000) if expected_super else
                    (o.logical_port.startswith('HS') and o.tested_speed == '480Mbps')) for o in selected):
            raise BuildPlanError('USB map lacks a physical USB-A speed companion test')
    controllers = {o.controller_id for o in selected}
    if len(controllers) != 1 or not controllers <= controller_slots:
        raise BuildPlanError('USB map controller is not uniquely bound to the observed machine')
    ports: dict[str, dict[str, object]] = {}
    used: dict[int, str] = {}
    first_label = ''
    for o in selected:
        if not re.fullmatch(r'(HS|SS)\d{2}', o.logical_port) or o.port_address is None or addresses.get(canonical_namespace(o.namespace_path)) != o.port_address:
            raise BuildPlanError('USB map port lacks corroborated literal firmware address proof')
        if canonical_namespace(o.namespace_path).split('.')[-2:] != ['XHC', o.logical_port]:
            raise BuildPlanError('USB map namespace does not match the reviewed XHC controller')
        if o.port_address in used and used[o.port_address] != o.logical_port:
            raise BuildPlanError('USB map duplicates a firmware port address')
        used[o.port_address] = o.logical_port
        connector = 255 if o.internal_device else 3
        if o.connector_type == 'internal' and not o.internal_device:
            raise BuildPlanError('USB map internal connector lacks internal-device proof')
        ports[o.logical_port] = {'UsbConnector': connector, 'port': o.port_address.to_bytes(4, 'little')}
        if o.logical_port == first_route and not o.internal_device and o.connector_type == 'USB-A' and o.logical_port.startswith('SS'):
            first_label = o.physical_label
    if not first_label or len(ports) > 15:
        raise BuildPlanError('USB map has no proven first-install route or exceeds the controller limit')
    properties = {'port-count': max(used).to_bytes(4, 'little'), 'ports': ports}
    personality = {'CFBundleIdentifier': 'com.apple.driver.AppleUSBHostMergeProperties',
        'IOClass': 'AppleUSBHostMergeProperties', 'IOProviderClass': 'AppleUSBXHCIPCI',
        'IONameMatch': 'XHC', 'IOProviderMergeProperties': properties, 'model': smbios}
    info = {'CFBundleDevelopmentRegion': 'English', 'CFBundleIdentifier': 'org.librecore.macloader.USBMap',
        'CFBundleInfoDictionaryVersion': '6.0', 'CFBundleName': 'MacLoaderUSBMap',
        'CFBundlePackageType': 'KEXT', 'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1.0',
        'OSBundleRequired': 'Root', 'IOKitPersonalities': {'MacLoader-XHC': personality}}
    payload = plistlib.dumps(info, sort_keys=True)
    return GeneratedUsbMap(payload, hashlib.sha256(payload).hexdigest(), evidence.to_evidence_record().digest, first_label)
