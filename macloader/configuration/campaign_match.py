"""Complete software candidate match requires positively observed facts."""
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Optional

from macloader.configuration.observations import hardware_facts
from macloader.domain.hardware import HardwareSnapshot

if TYPE_CHECKING:
    from macloader.database.campaigns import ReferenceCampaign
    from macloader.database.loader import Database


@dataclass(frozen=True)
class CampaignMatch:
    campaign: Optional["ReferenceCampaign"]
    unknown: tuple[str, ...] = ()
    mismatches: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.campaign is not None and not self.unknown and not self.mismatches


def match_campaign(snapshot: HardwareSnapshot, db: "Database") -> CampaignMatch:
    candidate = db.candidate_campaign(snapshot)
    if candidate is None:
        return CampaignMatch(None, mismatches=("machine/firmware",))
    facts = hardware_facts(snapshot)
    unknown: list[str] = []
    mismatches: list[str] = []
    fields = {"graphics": "graphics.igpu", "audio": "audio.codec", "wifi": "wifi.identity",
              "ethernet": "ethernet.identity", "bluetooth": "bluetooth.identity"}
    for category, refs in candidate.required_components.items():
        field = fields.get(category)
        if field is None:
            mismatches.append(category)
            continue
        observed = facts[field]
        if observed is None:
            unknown.append(field)
            continue
        allowed: set[str] = set()
        for ref in refs:
            rules = db.components[ref].match_rules
            key = "codec_ids" if category == "audio" else "usb_ids" if category == "bluetooth" else "pci_ids"
            allowed.update(str(value).lower() for value in rules.get(key, []))
        values = [observed] if isinstance(observed, str) else observed
        if not isinstance(values, list) or not values or not all(value in allowed for value in values):
            mismatches.append(field)
    panel = re.fullmatch(r"internal-[a-z]+-(\d+x\d+)-(non-touch|touch)", candidate.profile.graphics.panel_scope)
    if panel is None:
        mismatches.append("panel.policy")
    else:
        for field, expected in (("panel.resolution", [panel.group(1)]), ("panel.touch", [panel.group(2) == "touch"])):
            if facts[field] is None:
                unknown.append(field)
            elif facts[field] != expected:
                mismatches.append(field)
    if facts["graphics.dgpus"] is None:
        unknown.append("graphics.dgpus")
    elif facts["graphics.dgpus"]:
        mismatches.append("graphics.dgpus")
    expected_subsystem = candidate.profile.audio.codec.split("/")[-1].lower()
    if facts["audio.subsystem"] is None:
        unknown.append("audio.subsystem")
    elif facts["audio.subsystem"] != [expected_subsystem]:
        mismatches.append("audio.subsystem")
    model = db.models[candidate.model_id]
    if snapshot.cpu is None:
        unknown.append("cpu.identity")
    elif snapshot.cpu.vendor != "GenuineIntel" or snapshot.cpu.generation not in model.cpu_generations:
        mismatches.append("cpu.identity")
    for field in ("storage.identity", "input.topology", "usb_controllers.identity"):
        if facts[field] is None:
            unknown.append(field)
    return CampaignMatch(candidate, tuple(unknown), tuple(mismatches))
