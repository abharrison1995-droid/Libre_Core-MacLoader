"""Campaign media preparation over the existing guarded writer boundary."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Callable, Protocol

from macloader.domain.contracts import BuildManifest
from macloader.evidence.acpi_capture import CaptureError
from macloader.recovery.smoke import SmokeRecoveryService, VerifiedSmokeRecovery
from macloader.removable.adapters import AdapterStatus, current_adapter
from macloader.removable.writer import DestructiveConfirmation, MediaBindings, RemovableDevice, RemovableMediaWriter, WritePlan
from macloader.workflow.service import WorkflowService


class GuidedMediaAdapter(Protocol):
    @property
    def status(self) -> AdapterStatus: ...
    def enumerate(self) -> list[RemovableDevice]: ...
    def writer(self) -> RemovableMediaWriter: ...


@dataclass(frozen=True)
class MediaSelection:
    label: str
    plan: WritePlan


class GuidedMediaService:
    def __init__(self, adapter: GuidedMediaAdapter | None = None):
        self.adapter = adapter or current_adapter()
        self.source: Path | None = None
        self.bindings: MediaBindings | None = None
        self.selections: tuple[MediaSelection, ...] = ()
        self.selected: MediaSelection | None = None  # Never persisted consent.

    @property
    def qualified(self) -> bool:
        # ADR-007 remains Windows-first; adapter self-attestation alone does not
        # supply the safe-eject operation needed by the guided contract.
        return (self.adapter.status.qualified and self.adapter.status.platform == 'windows'
            and getattr(self.adapter, 'guided_eject_available', False) is True
            and callable(getattr(self.adapter, 'safe_eject', None)))

    def prepare(self, destination: Path, efi: Path, recovery: Path, manifest: BuildManifest,
                record: VerifiedSmokeRecovery, service: SmokeRecoveryService,
                configuration_digest: str, campaign_digest: str) -> None:
        service.verify(record, recovery, record.binding_digest)
        bindings = MediaBindings.from_smoke(manifest, record, configuration_digest, campaign_digest)
        WorkflowService._ensure_private_directory(destination.parent)
        # An interrupted copy is never adopted without full source verification.
        if destination.exists():
            try:
                RemovableMediaWriter._validate_published_artifacts(destination, bindings, require_all=True)
            except Exception:
                import shutil
                shutil.rmtree(destination)
        if not destination.exists():
            WorkflowService._ensure_private_directory(destination)
            RemovableMediaWriter._copy_no_follow(efi, destination)
            WorkflowService._ensure_private_directory(destination / 'Recovery')
            RemovableMediaWriter._copy_no_follow(recovery / 'smoke.json', destination / 'Recovery/smoke.json')
            for directory in ('Recovery', 'com.apple.recovery.boot'):
                WorkflowService._ensure_private_directory(destination / directory)
                for name in ('BaseSystem.dmg', 'BaseSystem.chunklist'):
                    RemovableMediaWriter._copy_no_follow(recovery / name, destination / directory / name)
            for path in destination.rglob('*'):
                if path.is_dir():
                    WorkflowService._ensure_private_directory(path)
                else:
                    WorkflowService._protect_private_file(path)
        RemovableMediaWriter._validate_published_artifacts(destination, bindings, require_all=True)
        self.source, self.bindings = destination, bindings

    def discover(self) -> tuple[str, ...]:
        if not self.qualified:
            raise CaptureError('WRITER_UNQUALIFIED', 'Preparation is saved. ADR-007 requires a physically qualified Windows writer with full readback and safe eject before a USB can be erased.')
        if self.source is None or self.bindings is None:
            raise ValueError('Media sources are not prepared')
        self.selected = None
        selections = []
        seen: set[str] = set()
        writer = self.adapter.writer()
        required = sum(item.size_bytes for item in writer._source_manifest(self.source)) + 512 * 1024 * 1024
        for device in self.adapter.enumerate():
            if device.is_system_disk or not device.is_removable or device.mounted or device.read_only or not device.whole_device or not device.serial:
                continue
            # Public identifiers bind stable properties; raw serial stays private.
            ref = hashlib.sha256(f'{device.vendor}:{device.model}:{device.serial}:{device.capacity_bytes}'.encode()).hexdigest()[:8]
            label = f'{device.model} · {device.capacity_bytes / (1024 ** 3):.1f} GiB · USB {ref}'
            if label in seen:
                raise CaptureError('MEDIA_IDENTITY_AMBIGUOUS', 'Two USB devices have indistinguishable stable identities. Disconnect one and retry; nothing was erased.')
            seen.add(label)
            try:
                plan = writer.dry_run(device, required, self.source, self.bindings)
            except Exception:
                continue
            selections.append(MediaSelection(label, plan))
        self.selections = tuple(selections)
        return tuple(item.label for item in selections)

    def select(self, label: str) -> None:
        self.selected = next(item for item in self.selections if item.label == label)

    def write(self, cancel: Callable[[], bool], mark_started: Callable[[], None]) -> WritePlan:
        if not self.qualified or self.source is None or self.selected is None:
            raise ValueError('No currently qualified selected USB')
        plan = self.selected.plan
        writer = self.adapter.writer()
        confirmation = DestructiveConfirmation.issue(plan)
        setter = getattr(self.adapter, "set_cancel", None)
        if callable(setter):
            setter(cancel)
        # Journal conservatively before crossing the guarded destructive boundary.
        mark_started()
        try:
            writer.write(plan, self.source, confirmation, cancel=cancel)
            eject = getattr(self.adapter, 'safe_eject')
            eject(plan.target)
        except Exception as exc:
            writer._invalidate(plan, 'Guided write/readback/eject failed')
            raise CaptureError('MEDIA_WRITE_FAILED', 'USB preparation failed. The selected USB may have been erased and is not ready to boot. Readback/eject did not complete; saved source files are preserved. Reconnect that USB and retry.') from exc
        finally:
            if callable(setter):
                setter(lambda: False)
            self.selected = None
        return plan
