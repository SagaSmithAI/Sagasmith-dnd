"""Source-reviewed 2014 water state, weapon restrictions and immersion defense."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

WATER_RULE = "dnd5e.core.combat.underwater"


def validate_water_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "underwater", "fully_immersed", "resolution_id", "rule_receipts",
    }:
        raise ValueError("water state has unsupported fields")
    if any(type(value.get(key)) is not bool for key in ("underwater", "fully_immersed")):
        raise ValueError("water state requires boolean underwater and fully_immersed")
    if value["fully_immersed"] and not value["underwater"]:
        raise ValueError("a fully immersed actor must be underwater")
    if "resolution_id" in value and (
        not isinstance(value["resolution_id"], str) or not value["resolution_id"]
    ):
        raise ValueError("water resolution_id must be a nonempty string")
    if "rule_receipts" in value and (
        not isinstance(value["rule_receipts"], list)
        or any(not isinstance(item, dict) or item.get("mechanic_id") != WATER_RULE
               for item in value["rule_receipts"])
    ):
        raise ValueError("water receipts must identify the underwater source boundary")
    return deepcopy(value)


def water_state(sheet: dict[str, Any]) -> dict[str, Any]:
    if sheet.get("edition", "2014") != "2014":
        return {"underwater": False, "fully_immersed": False}
    value = dict(sheet.get("combat") or {}).get("water_environment")
    return validate_water_state(value) if value is not None else {
        "underwater": False, "fully_immersed": False,
    }


def underwater_weapon_rule(
    sheet: dict[str, Any], weapon: dict[str, Any], *, attack_mode: str,
    swim_speed: int, range_result: dict[str, Any],
) -> dict[str, Any] | None:
    if not water_state(sheet)["underwater"] or weapon.get("spell_attack"):
        return None
    # These are names on the selected authoritative weapon card, never a
    # caller-supplied exemption. Unknown/custom weapons receive no exception.
    name = str(weapon.get("name") or "").strip().casefold().replace(" ", "_")
    source = str(dict(weapon.get("base_weapon_source") or {}).get("artifact_id")
                 or weapon.get("source_key") or "").rsplit(":", 1)[-1]
    if source.startswith("dnd5e.content.srd2014.item."):
        name = source.removeprefix("dnd5e.content.srd2014.item.").replace("-", "_")
    melee_exceptions = {"dagger", "javelin", "shortsword", "spear", "trident"}
    ranged_exceptions = {
        "crossbow,_light", "crossbow,_hand", "crossbow,_heavy",
        "light_crossbow", "hand_crossbow", "heavy_crossbow", "net", "javelin", "spear",
        "trident", "dart", "crossbow_light", "crossbow_hand", "crossbow_heavy",
    }
    automatic_miss = False
    if attack_mode == "ranged":
        if not range_result.get("enforced"):
            # Import lazily: the shared combat pipeline imports this pure state module.
            from .combat_engine import NeedsRulingError

            raise NeedsRulingError(
                "underwater ranged attacks require an authoritative range classification",
                missing=("underwater.attack_range",), ruling_kind="agent_dm_adjudication",
            )
        automatic_miss = bool(range_result["disadvantage"])
        disadvantage = name not in ranged_exceptions
    else:
        disadvantage = swim_speed <= 0 and name not in melee_exceptions
    return {"mechanic_id": WATER_RULE, "automatic_miss": automatic_miss,
            "disadvantage": disadvantage, "weapon_name": weapon.get("name"),
            "swim_speed": swim_speed}
