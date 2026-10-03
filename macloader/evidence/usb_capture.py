"""Physical actions correlated with observed kernel events; numbering is never guessed."""
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Callable, Protocol

from macloader.domain.evidence import EvidenceConfidence
from macloader.evidence.acpi_capture import CaptureError
from macloader.evidence.usb import UsbEvidenceSession, UsbPortObservation


@dataclass(frozen=True)
class UsbDeviceEvent:
    event_id: str
    device_token: str
    controller: str
    namespace_path: str
    logical_port: str
    port_address: int | None
    speed_mbps: int
    internal: bool = False


class UsbEventProvider(Protocol):
    def enumerate(self) -> tuple[UsbDeviceEvent, ...]: ...


def canonical_namespace(path: str) -> str:
    return ".".join(part.rstrip("_") for part in path.lstrip("\\").split("."))


def firmware_usb_addresses(dsl: str) -> dict[str, int]:
    """Only literal Name(_ADR, integer) directly inside named XHC ports.

    Computed addresses, methods, aliases and ambiguous definitions stay unknown.
    This parser never executes AML. Paths are corroborated with kernel firmware nodes.
    """
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', dsl, flags=re.S)
    token = re.compile(r'(Device|Scope|Method)\s*\(\s*([\\A-Za-z0-9_.]+)[^)]*\)\s*\{|Name\s*\(\s*_ADR\s*,\s*(0x[0-9A-Fa-f]+|\d+|One|Zero)\s*\)|[{}]')
    stack: list[tuple[str, bool]] = []
    addresses: dict[str, int] = {}
    for item in token.finditer(text):
        if item.group(1):
            parent = stack[-1][0] if stack else ""
            name = item.group(2)
            path = name.lstrip('\\') if name.startswith('\\') else '.'.join(p for p in (parent, name) if p)
            stack.append((path, item.group(1) == "Method" or bool(stack and stack[-1][1])))
        elif item.group(3) and stack and not stack[-1][1]:
            path = stack[-1][0]
            if re.fullmatch(r'_SB(?:_)?\.[A-Za-z0-9_]+\.XHC[I_]?\.(HS|SS)\d{2}', path):
                literal = item.group(3)
                value = {"One": 1, "Zero": 0}.get(literal)
                value = int(literal, 0) if value is None else value
                path = canonical_namespace(path)
                if path in addresses or not 1 <= value <= 255:
                    raise CaptureError("USB_FIRMWARE_AMBIGUOUS", "Firmware USB addresses are ambiguous.")
                addresses[canonical_namespace(path)] = value
        elif item.group() == "{":
            stack.append((stack[-1][0] if stack else "", True))
        elif item.group() == "}" and stack:
            stack.pop()
    return addresses


class LinuxUsbEventProvider:
    def __init__(self, key: str, addresses: dict[str, int], root: Path = Path('/sys/bus/usb/devices')):
        self.key, self.addresses, self.root = key, addresses, root

    @staticmethod
    def _text(path: Path) -> str:
        try:
            return path.read_text()[:4096].strip()
        except OSError:
            return ""

    def enumerate(self) -> tuple[UsbDeviceEvent, ...]:
        if not self.root.is_dir():
            raise CaptureError("USB_PROVIDER_UNAVAILABLE", "USB observation is unavailable on this host.")
        result = []
        for entry in sorted(self.root.iterdir()):
            if not re.fullmatch(r'\d+-\d+', entry.name):
                continue  # root-attached devices only; external hub ancestry is not physical proof
            device = entry.resolve()
            vendor, product = self._text(device / 'idVendor'), self._text(device / 'idProduct')
            if not re.fullmatch(r'[0-9a-fA-F]{4}', vendor) or not re.fullmatch(r'[0-9a-fA-F]{4}', product):
                continue
            port_no = entry.name.split('-')[1]
            bus = entry.name.split('-')[0]
            port = device.parent / f'{bus}-0:1.0' / f'usb{bus}-port{port_no}'
            namespace = canonical_namespace(self._text(port / 'firmware_node' / 'path'))
            logical = namespace.rsplit('.', 1)[-1] if re.fullmatch(r'.*\.(HS|SS)\d{2}', namespace) else ""
            controller = next((p.name for p in device.parents if re.fullmatch(r'\d{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d', p.name)), "")
            speed = self._text(device / 'speed')
            if not speed.replace('.', '', 1).isdigit():
                continue
            # Raw serial is transient; only a private keyed token survives enumeration.
            identity = ':'.join((vendor, product, self._text(device / 'serial')))
            token = hmac.new(self.key.encode(), identity.encode(), hashlib.sha256).hexdigest()
            result.append(UsbDeviceEvent(entry.name, token, controller, namespace, logical,
                                         self.addresses.get(namespace), int(float(speed)),
                                         self._text(port / 'connect_type') == 'hardwired'))
        return tuple(result)


@dataclass(frozen=True)
class UsbCaptureStep:
    label: str
    connector: str
    speed: str
    orientation: str | None = None

    @property
    def instruction(self) -> str:
        speed = 'USB 3' if self.speed == 'super' else 'USB 2'
        orientation = f', {self.orientation} orientation' if self.orientation else ''
        return f'Insert the {speed} test device into {self.label}{orientation}. MacLoader is watching for insertion.'


class UsbEvidenceCollector:
    """Resumable wizard progress wraps the existing UsbEvidenceSession contract."""
    def __init__(self, session: UsbEvidenceSession, steps: tuple[UsbCaptureStep, ...], provider: UsbEventProvider,
                 persist: Callable[[dict[str, object]], None], progress: dict[str, object] | None = None):
        self.session, self.steps, self.provider, self.persist = session, steps, provider, persist
        saved = progress or {}
        self.cursor = int(str(saved.get('cursor', 0)))
        tokens = saved.get('tokens', {})
        if not isinstance(tokens, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in tokens.items()):
            raise ValueError('Invalid USB wizard tokens')
        self.tokens: dict[str, str] = dict(tokens)
        if not 0 <= self.cursor <= len(steps):
            raise ValueError('Invalid USB wizard cursor')
        self.phase = 'remove'  # restart requires unplugging before arming a new insertion
        self.baseline: set[str] = set()
        self.unresolved = list(session.unresolved_logical_correlation)
        self._save()

    @property
    def instruction(self) -> str:
        if self.cursor == len(self.steps):
            return 'Physical port checks finished.'
        return 'Remove the test device before the next port check.' if self.phase == 'remove' else self.steps[self.cursor].instruction

    def _save(self) -> None:
        self.persist(dict(cursor=self.cursor, tokens=self.tokens, evidence=self.session.to_dict()))

    def poll(self) -> bool:
        if self.cursor == len(self.steps):
            return True
        devices = self.provider.enumerate()
        external = [d for d in devices if not d.internal]
        if self.phase == 'remove':
            if external:
                return False
            self.baseline = {d.event_id for d in devices}
            self.phase = 'insert'
            return False
        inserted = [d for d in external if d.event_id not in self.baseline]
        if not inserted:
            return False
        if len(inserted) != 1:
            raise CaptureError('USB_INSERTION_AMBIGUOUS', 'Connect only one test device directly to the requested socket.')
        event = inserted[0]
        step = self.steps[self.cursor]
        speed = 'super' if event.speed_mbps >= 5000 else 'high' if event.speed_mbps == 480 else 'unknown'
        if speed != step.speed:
            raise CaptureError('USB_SPEED_UNPROVEN', 'The device did not negotiate the requested speed. Check the test device or cable.')
        token = self.tokens.get(speed)
        if token is not None and token != event.device_token:
            raise CaptureError('USB_TEST_DEVICE_CHANGED', 'Use the same test device for this speed throughout the port checks.')
        self.tokens[speed] = event.device_token
        if not event.logical_port or not event.controller or event.port_address is None:
            if step.connector != 'USB-C':
                raise CaptureError('USB_ROUTE_UNPROVEN', 'MacLoader cannot prove this socket’s firmware route. No logical port was guessed; open Engineering diagnostics.')
            self.unresolved.append(step.label)
        else:
            observation = UsbPortObservation(step.label, event.logical_port, step.connector,
                f'{event.speed_mbps}Mbps', event.controller, step.orientation, False,
                port_address=event.port_address, namespace_path=event.namespace_path)
            if observation in self.session.observations:
                raise CaptureError('USB_DUPLICATE_OBSERVATION', 'This physical step was already recorded.')
            self.session = replace(self.session, observations=(*self.session.observations, observation))
        self.session = replace(self.session, unresolved_logical_correlation=tuple(sorted(set(self.unresolved))), confidence=EvidenceConfidence.HIGH)
        self.cursor += 1
        self.phase = 'remove'
        self._save()
        return self.cursor == len(self.steps)


def collect_firmware_usb_addresses(capture: Path, compiler: Path, compiler_digest: str,
                                   work: Path, cancel: Callable[[], bool]) -> dict[str, int]:
    """Disassemble privately with the pinned compiler; retain only literal route addresses."""
    import tempfile
    from macloader.build.acpi import AcpiProcessor
    from macloader.workflow.service import WorkflowService
    processor = AcpiProcessor(compiler, compiler_digest, work)
    processor._verify_tool()
    WorkflowService._ensure_private_directory(work)
    addresses: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix='.usb-firmware-', dir=work) as temporary:
        root = Path(temporary)
        for table in AcpiProcessor._find_tables(capture / 'PRIVATE-ACPI'):
            AcpiProcessor._validate_table(table)
            prefix = root / table.stem
            result = processor._run(['-d', '-p', str(prefix), str(table)], prefix.with_suffix('.log'), cancel=cancel)
            if result[0] or not prefix.with_suffix('.dsl').is_file():
                raise CaptureError('USB_FIRMWARE_UNAVAILABLE', 'Firmware USB route proof could not be extracted with the verified tool.')
            observed = firmware_usb_addresses(prefix.with_suffix('.dsl').read_text(errors='replace'))
            if set(addresses) & set(observed):
                raise CaptureError('USB_FIRMWARE_AMBIGUOUS', 'Firmware repeats USB route definitions; no route was guessed.')
            addresses.update(observed)
    return addresses
