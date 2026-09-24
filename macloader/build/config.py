"""Deterministic OpenCore configuration transforms from the pinned schema."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import plistlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, cast
import yaml

from macloader.dependencies.cache import compute_file_sha256
from macloader.domain.contracts import canonical_json_digest
from macloader.exceptions import BuildPlanError


@dataclass(frozen=True)
class ReviewedGraphicsPolicy:
    device_path: str
    platform_id: bytes
    device_id: bytes
    panel_scope: str
    source_refs: Tuple[str, ...]


@dataclass(frozen=True)
class ReviewedAudioPolicy:
    device_path: str
    codec: str
    layout_id: int
    source_refs: Tuple[str, ...]


@dataclass(frozen=True)
class ReviewedUsbPolicy:
    routes: Tuple[str, ...]
    first_install_route: str
    usb_c_correlation: str


@dataclass(frozen=True)
class ReviewedEfiProfile:
    profile_id: str
    model_id: str
    product_id: str
    version: str
    build: str
    bios_binding: str
    graphics: ReviewedGraphicsPolicy
    audio: ReviewedAudioPolicy
    usb: ReviewedUsbPolicy
    wwan_policy: str
    acpi_source_scope: str
    source_digest: str


def effective_profile_digest(
    profile: ReviewedEfiProfile, effective_options: Mapping[str, str]
) -> str:
    """Return the canonical identity of the profile after accepted selections.

    ``ReviewedEfiProfile.source_digest`` identifies the raw reviewed YAML.
    BuildPlan and generated artifacts must instead bind the effective profile,
    including material user selections, so a changed option cannot be accepted
    while the builder compares two different identities.
    """
    return canonical_json_digest({
        "profile": profile.source_digest,
        "options": [[key, effective_options[key]] for key in sorted(effective_options)],
    })


def _data_bytes(value: str, label: str) -> bytes:
    try:
        result = bytes.fromhex(value)
    except ValueError as exc:
        raise BuildPlanError(f"Reviewed {label} value is not hexadecimal data") from exc
    if not result:
        raise BuildPlanError(f"Reviewed {label} value is empty")
    return result


def load_reviewed_profile(path: Optional[Path] = None) -> ReviewedEfiProfile:
    profile_path = path or Path(__file__).parent.parent / "database" / "data" / "profiles" / "t480s" / "p4-sequoia.yaml"
    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BuildPlanError(f"Unable to load reviewed EFI profile: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != "1":
        raise BuildPlanError("Reviewed EFI profile has an unsupported schema")
    graphics = data.get("graphics")
    audio = data.get("audio")
    usb = data.get("usb")
    if not all(isinstance(item, dict) for item in (graphics, audio, usb)):
        raise BuildPlanError("Reviewed EFI profile is missing graphics, audio, or USB policy")
    graphics = cast(Dict[str, Any], graphics)
    audio = cast(Dict[str, Any], audio)
    usb = cast(Dict[str, Any], usb)
    required = {"schema_version", "profile_id", "model_id", "product_id", "version", "build", "bios_binding", "graphics", "audio", "usb", "wwan_policy", "acpi_source_scope"}
    if set(data) != required:
        raise BuildPlanError("Reviewed EFI profile contains unknown or missing fields")
    for key in ("source_refs",):
        if not isinstance(graphics.get(key), list) or not all(isinstance(item, str) for item in graphics[key]):
            raise BuildPlanError("Reviewed graphics source references are invalid")
        if not isinstance(audio.get(key), list) or not all(isinstance(item, str) for item in audio[key]):
            raise BuildPlanError("Reviewed audio source references are invalid")
    routes = usb.get("routes")
    if not isinstance(routes, list) or not all(isinstance(item, str) for item in routes):
        raise BuildPlanError("Reviewed USB routes are invalid")
    if usb.get("first_install_route") not in routes or usb.get("usb_c_correlation") != "unresolved":
        raise BuildPlanError("Reviewed USB policy must preserve the unresolved USB-C correlation")
    canonical = dict(data)
    source_digest = canonical_json_digest(canonical)
    return ReviewedEfiProfile(
        profile_id=str(data["profile_id"]), model_id=str(data["model_id"]), product_id=str(data["product_id"]),
        version=str(data["version"]), build=str(data["build"]), bios_binding=str(data["bios_binding"]),
        graphics=ReviewedGraphicsPolicy(
            device_path=str(graphics["device_path"]),
            platform_id=_data_bytes(str(graphics["platform_id"]), "graphics platform-id"),
            device_id=_data_bytes(str(graphics["device_id"]), "graphics device-id"),
            panel_scope=str(graphics["panel_scope"]), source_refs=tuple(graphics["source_refs"]),
        ),
        audio=ReviewedAudioPolicy(
            device_path=str(audio["device_path"]), codec=str(audio["codec"]), layout_id=int(audio["layout_id"]),
            source_refs=tuple(audio["source_refs"]),
        ),
        usb=ReviewedUsbPolicy(
            routes=tuple(routes), first_install_route=str(usb["first_install_route"]),
            usb_c_correlation=str(usb["usb_c_correlation"]),
        ),
        wwan_policy=str(data["wwan_policy"]), acpi_source_scope=str(data["acpi_source_scope"]), source_digest=source_digest,
    )


class SchemaDrivenConfigGenerator:
    """Apply reviewed values to OpenCore's exact pinned 1.0.7 schema."""

    def __init__(self, sample_path: Path, sample_sha256: str) -> None:
        self.sample_path = Path(sample_path).resolve()
        self.sample_sha256 = sample_sha256.lower()
        self.last_binding_digest = ""

    @property
    def schema_digest(self) -> str:
        return self.sample_sha256

    def _load_schema(self) -> Dict[str, Any]:
        if self.sample_path.is_symlink() or not self.sample_path.is_file():
            raise BuildPlanError("Pinned OpenCore Sample.plist is missing or unsafe")
        if compute_file_sha256(self.sample_path) != self.sample_sha256:
            raise BuildPlanError("Pinned OpenCore Sample.plist digest does not match the trusted toolchain")
        try:
            schema = plistlib.loads(self.sample_path.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise BuildPlanError(f"Pinned OpenCore Sample.plist cannot be read: {exc}") from exc
        if not isinstance(schema, dict) or not {"ACPI", "Booter", "DeviceProperties", "Kernel", "Misc", "NVRAM", "PlatformInfo", "UEFI"}.issubset(schema):
            raise BuildPlanError("Pinned OpenCore schema is missing required sections")
        return deepcopy(schema)

    @staticmethod
    def _kernel_entry(bundle_path: str) -> Dict[str, Any]:
        leaf = bundle_path.rsplit("/", 1)[-1]
        executable = leaf.removesuffix(".kext")
        return {
            "Arch": "Any", "BundlePath": bundle_path, "Comment": "P4 reviewed dependency",
            "Enabled": True, "ExecutablePath": f"Contents/MacOS/{executable}",
            "MaxKernel": "", "MinKernel": "8.0.0", "PlistPath": "Contents/Info.plist",
        }

    @staticmethod
    def _ordered_kexts(kexts: Iterable[str]) -> Tuple[str, ...]:
        """Keep parent kexts before their bundled plug-ins and dependants."""
        unique = {item for item in kexts}

        def order(item: str) -> Tuple[int, str]:
            name = Path(item).name.casefold()
            if name == "lilu.kext":
                return (0, name)
            if name == "virtualsmc.kext":
                return (1, name)
            if name in {"applealc.kext", "bluetoolfixup.kext", "whatevergreen.kext"}:
                return (2, name)
            if name.startswith("smc") and name.endswith(".kext"):
                return (3, name)
            return (4, name)

        return tuple(sorted(unique, key=order))

    @staticmethod
    def _driver_entry(path: str, enabled: bool = True) -> Dict[str, Any]:
        return {"Arguments": "", "Comment": "P4 reviewed OpenCore driver", "Enabled": enabled, "LoadEarly": False, "Path": path}

    def generate(
        self,
        profile: ReviewedEfiProfile,
        *,
        identity: Mapping[str, str],
        kexts: Iterable[str],
        drivers: Iterable[str],
        acpi_files: Iterable[str],
        opencore_version: str,
        acpi_digest: str,
        evidence_digests: Iterable[str],
        effective_options: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        # The reviewed profile supplies the safe structural defaults; accepted
        # user selections are projected into this effective profile before any
        # plist content is emitted.  A digest-only change is never treated as
        # an applied setting.
        selected_profile = profile
        if effective_options is not None:
            audio = effective_options.get("profile.audio", "")
            if audio:
                match = re.fullmatch(r"layout-(\d+)", audio)
                if match is None:
                    raise BuildPlanError("Effective audio selection is not a policy layout ID")
                selected_profile = replace(
                    profile,
                    audio=replace(profile.audio, layout_id=int(match.group(1))),
                )
            smbios = effective_options.get("profile.smbios")
            if smbios and smbios != profile.model_id and smbios != "MacBookPro15,2":
                raise BuildPlanError("Effective SMBIOS selection is not supported by the reviewed profile")
        profile = selected_profile
        config = self._load_schema()
        config["#WARNING - 1"] = "Generated by Libre_Core MacLoader from the pinned OpenCore 1.0.7 schema."
        config["#WARNING - 2"] = "Machine-bound T480s 20L8 policy; validate with the matching trusted ocvalidate."
        config["#WARNING - 3"] = "USB-C logical correlation is intentionally unresolved; do not treat this as a complete USB map."
        config["#WARNING - 4"] = "Experimental graphics/audio policy requires physical acceptance."

        config["Kernel"]["Add"] = [self._kernel_entry(item) for item in self._ordered_kexts(kexts)]
        config["UEFI"]["Drivers"] = [self._driver_entry(item) for item in sorted(set(drivers))]
        config["ACPI"]["Add"] = [
            {"Comment": "P4 reviewed machine-bound ACPI source", "Enabled": True, "Path": item}
            for item in sorted(set(acpi_files))
        ]
        config["ACPI"]["Delete"] = []
        config["ACPI"]["Patch"] = []

        config["DeviceProperties"]["Add"] = {
            profile.graphics.device_path: {
                "AAPL,ig-platform-id": profile.graphics.platform_id,
                "device-id": profile.graphics.device_id,
            },
            profile.audio.device_path: {"layout-id": int(profile.audio.layout_id).to_bytes(4, "little")},
        }
        config["DeviceProperties"]["Delete"] = {}

        nvram_uuid = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
        nvram = config["NVRAM"].setdefault("Add", {}).setdefault(nvram_uuid, {})
        nvram["boot-args"] = f"alcid={profile.audio.layout_id}"
        config["NVRAM"]["Add"][nvram_uuid] = nvram

        generic = config["PlatformInfo"]["Generic"]
        for key, value in identity.items():
            if key == "ROM":
                generic[key] = bytes.fromhex(value)
            else:
                generic[key] = value
        generic["SystemProductName"] = identity["SystemProductName"]
        config["PlatformInfo"]["Automatic"] = True
        config["PlatformInfo"]["UpdateDataHub"] = True
        config["PlatformInfo"]["UpdateNVRAM"] = True
        config["PlatformInfo"]["UpdateSMBIOS"] = True
        config["PlatformInfo"]["UpdateSMBIOSMode"] = "Create"
        # The schema carries no OpenCore-version key. The selected toolchain
        # and archive lock bind the version outside the plist itself.
        config.pop("OC", None)

        # This metadata is not consumed by OpenCore; it is used by the
        # manifest and is deliberately kept outside the plist schema.
        config_digest = canonical_json_digest({
            "profile": profile.source_digest,
            "schema": self.schema_digest,
            "opencore_version": opencore_version,
            "acpi_digest": acpi_digest,
            "evidence_digests": sorted(evidence_digests),
            "wwan_policy": profile.wwan_policy,
            "usb": profile.usb.__dict__,
        })
        # Keep the binding digest in the build manifest rather than adding a
        # non-OpenCore top-level plist key that the real validator may reject.
        self.last_binding_digest = config_digest
        return config

    def generate_synthetic_for_test(
        self,
        *,
        identity: Mapping[str, str],
        kexts: Iterable[str],
        drivers: Iterable[str],
    ) -> Dict[str, Any]:
        """Build an unprofiled schema-shaped config for synthetic integration tests only."""
        config = self._load_schema()
        config["#WARNING - MacLoader"] = "Synthetic test config; not machine-qualified or install-ready."
        config["Kernel"]["Add"] = [self._kernel_entry(item) for item in self._ordered_kexts(kexts)]
        config["UEFI"]["Drivers"] = [self._driver_entry(item) for item in sorted(set(drivers))]
        config["ACPI"]["Add"] = []
        config["ACPI"]["Delete"] = []
        config["ACPI"]["Patch"] = []
        generic = config["PlatformInfo"]["Generic"]
        for key, value in identity.items():
            generic[key] = bytes.fromhex(value) if key == "ROM" else value
        config["PlatformInfo"]["Automatic"] = True
        config["PlatformInfo"]["UpdateDataHub"] = True
        config["PlatformInfo"]["UpdateNVRAM"] = True
        config["PlatformInfo"]["UpdateSMBIOS"] = True
        config["PlatformInfo"]["UpdateSMBIOSMode"] = "Create"
        return config

    def write(self, config: Dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(config, handle, sort_keys=False)
