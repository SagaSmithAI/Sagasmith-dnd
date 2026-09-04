"""Pure Steel Defender lifecycle and hit-point mechanics.

The module deliberately deals in plain actor-card dictionaries.  A caller is
responsible for authorization, encounter membership, and committing the
returned copies atomically.  This keeps the source-specific Battle Smith
rules independent from the MCP transport and the general combat engine.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict
from math import isfinite
from typing import Any, Mapping

from sagasmith_dnd.conditions import INCAPACITATING_STATE_IDS, condition_ids
from sagasmith_dnd.engine import roll
from sagasmith_dnd.hit_points import apply_basic_healing_to_sheet
from sagasmith_dnd.resources import mutate_bounded_resource

STEEL_DEFENDER_RELATION_KEY = "steel_defender"
REPAIR_USES_MAX = 3
REVIVE_WINDOW_TICKS = 600
REVIVE_DELAY_TICKS = 10


class SteelDefenderError(ValueError):
    """Raised when a source-defined Steel Defender transition is illegal."""


def _require_2014(sheet: Mapping[str, Any], *, label: str) -> None:
    if str(sheet.get("edition") or "") != "2014":
        raise SteelDefenderError(f"{label} requires the 2014 rules edition")


def _distance(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or value < 0
    ):
        raise SteelDefenderError("distance_ft must be a non-negative number")
    return float(value)


def _find_repair_activity(
    sheet: Mapping[str, Any], activity_id: str | None,
) -> tuple[str, dict[str, Any]]:
    content = dict(sheet.get("content") or {})
    candidates: list[tuple[str, dict[str, Any]]] = []
    for section in ("activities", "features"):
        for item in content.get(section, []):
            if not isinstance(item, dict):
                continue
            if activity_id is not None and str(item.get("id") or "") == activity_id:
                return section, deepcopy(item)
            name = str(item.get("name") or "").strip().casefold()
            if activity_id is None and name.startswith("repair"):
                candidates.append((section, deepcopy(item)))
    if activity_id is not None:
        raise SteelDefenderError("Repair activity is not present on the defender card")
    if len(candidates) != 1:
        raise SteelDefenderError("the defender card must contain exactly one Repair activity")
    return candidates[0]


def _consume_repair_use(
    sheet: dict[str, Any], section: str, activity: dict[str, Any],
) -> dict[str, Any]:
    activation = dict(activity.get("activation") or {})
    if str(activation.get("type") or "").casefold() != "action":
        raise SteelDefenderError("Repair must have an action activation")
    resource_key = str(activity.get("resource_key") or "").strip()
    if resource_key:
        resource = dict(dict(sheet.get("resources") or {}).get(resource_key) or {})
        if not resource or bool(resource.get("unlimited", False)):
            raise SteelDefenderError("Repair requires three bounded daily uses")
        if int(resource.get("max", 0) or 0) != REPAIR_USES_MAX:
            raise SteelDefenderError("Repair resource maximum must be three")
        try:
            payment = mutate_bounded_resource(resource, amount=1, direction="spend")
        except ValueError as error:
            raise SteelDefenderError("Repair has no uses remaining") from error
        sheet.setdefault("resources", {})[resource_key] = resource
        return {"kind": "resource", "key": resource_key, **payment}

    uses = dict(activity.get("uses") or {})
    if not uses:
        raise SteelDefenderError("Repair requires its source-authored bounded uses")
    if bool(uses.get("unlimited", False)) or int(uses.get("max", 0) or 0) != REPAIR_USES_MAX:
        raise SteelDefenderError("Repair activity maximum must be three")
    try:
        payment = mutate_bounded_resource(uses, amount=1, direction="spend")
    except ValueError as error:
        raise SteelDefenderError("Repair has no uses remaining") from error
    activity["uses"] = uses
    sheet.setdefault("content", {}).setdefault(section, [])
    sheet["content"][section] = [
        activity if str(item.get("id") or "") == str(activity.get("id") or "") else item
        for item in sheet["content"][section]
    ]
    return {"kind": "activity_uses", **payment}


def _creature_type(sheet: Mapping[str, Any]) -> str:
    progression = dict(sheet.get("progression") or {})
    return str(progression.get("species") or sheet.get("creature_type") or "").casefold()


def _require_construct(sheet: Mapping[str, Any], *, label: str) -> None:
    if "construct" not in _creature_type(sheet):
        raise SteelDefenderError(f"{label} requires a construct Steel Defender")


def repair_steel_defender(
    defender_sheet: Mapping[str, Any],
    target_sheet: Mapping[str, Any] | None = None,
    *,
    proficiency_bonus: int,
    distance_ft: int | float = 0,
    target_kind: str = "self",
    activity_id: str | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve one Repair action and return updated defender/target copies.

    ``target_kind`` is explicit because an ordinary actor card cannot safely
    infer whether a non-player object is a construct.  Legal values are the
    defender itself, another construct, or an object.
    """

    _require_2014(defender_sheet, label="Repair")
    _require_construct(defender_sheet, label="Repair")
    if (
        isinstance(proficiency_bonus, bool)
        or not isinstance(proficiency_bonus, int)
        or not 2 <= proficiency_bonus <= 6
    ):
        raise SteelDefenderError("proficiency_bonus must be an integer from 2 to 6")
    normalized_kind = str(target_kind).strip().casefold().replace("-", "_")
    if normalized_kind not in {"self", "construct", "object"}:
        raise SteelDefenderError("Repair target_kind must be self, construct, or object")
    distance = _distance(distance_ft)
    if distance > 5:
        raise SteelDefenderError("Repair target must be within 5 feet")
    if target_sheet is None:
        target_sheet = defender_sheet
    if normalized_kind == "self" and target_sheet is not defender_sheet:
        raise SteelDefenderError("Repair self target must be the defender")
    if normalized_kind == "construct" and "construct" not in _creature_type(target_sheet):
        raise SteelDefenderError("Repair construct target must be a construct")
    if "dead" in condition_ids(target_sheet.get("conditions")):
        raise SteelDefenderError("Repair cannot restore a dead target")

    defender = deepcopy(dict(defender_sheet))
    target = defender if normalized_kind == "self" else deepcopy(dict(target_sheet))
    section, activity = _find_repair_activity(defender, activity_id)
    payment = _consume_repair_use(defender, section, activity)
    rolled = asdict(roll(f"2d8+{proficiency_bonus}", rng=rng))
    applied = apply_basic_healing_to_sheet(target, amount=int(rolled["total"]))
    if normalized_kind == "self":
        defender = applied["sheet"]
        target = defender
    else:
        target = applied["sheet"]
    return {
        "status": "committed",
        "defender_sheet": defender,
        "target_sheet": target,
        "target_kind": normalized_kind,
        "roll": rolled,
        "healing": applied,
        "payment": payment,
    }


def mending_steel_defender(
    defender_sheet: Mapping[str, Any], *, rng: Any = None,
) -> dict[str, Any]:
    """Apply the Battle Smith feature's 2d6 Mending healing to its defender."""

    _require_2014(defender_sheet, label="Mending")
    _require_construct(defender_sheet, label="Mending")
    if "dead" in condition_ids(defender_sheet.get("conditions")):
        raise SteelDefenderError("Mending healing cannot restore a dead defender")
    rolled = asdict(roll("2d6", rng=rng))
    applied = apply_basic_healing_to_sheet(
        deepcopy(dict(defender_sheet)), amount=int(rolled["total"])
    )
    return {
        "status": "committed",
        "sheet": applied["sheet"],
        "roll": rolled,
        "healing": applied,
    }


def _smith_tools_present(sheet: Mapping[str, Any]) -> bool:
    for item in dict(sheet.get("inventory") or {}).get("items", []):
        if not isinstance(item, Mapping) or int(item.get("quantity", 0) or 0) <= 0:
            continue
        name = (
            str(item.get("name") or "")
            .replace("â€™", "'")
            .replace("â€˜", "'")
            .strip()
            .casefold()
        )
        if name in {"smith's tools", "smiths tools"}:
            return True
    return False


def _slot_level(key: Any) -> int | None:
    match = re.fullmatch(r"(?:spell)?([1-9])", str(key).strip().casefold())
    return int(match.group(1)) if match else None


def _consume_spell_slot(sheet: dict[str, Any], slot_level: int) -> dict[str, Any]:
    if isinstance(slot_level, bool) or not isinstance(slot_level, int) or not 1 <= slot_level <= 9:
        raise SteelDefenderError("revival slot_level must be an integer from 1 to 9")
    spellcasting = dict(sheet.get("spellcasting") or {})
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for key, raw_resource in dict(spellcasting.get("spell_slots") or {}).items():
        level = _slot_level(key)
        resource = dict(raw_resource or {})
        if level == slot_level and int(resource.get("value", 0) or 0) > 0:
            candidates.append((level, str(key), resource))
    pact = dict(spellcasting.get("pact_magic") or {})
    pact_level = int(pact.get("slot_level", 0) or 0)
    if pact_level == slot_level and int(pact.get("value", 0) or 0) > 0:
        candidates.append((pact_level, "pact_magic", pact))
    if not candidates:
        raise SteelDefenderError("revival requires an available spell slot of 1st level or higher")
    level, key, resource = min(candidates, key=lambda item: item[1])
    try:
        payment = mutate_bounded_resource(resource, amount=1, direction="spend")
    except ValueError as error:
        raise SteelDefenderError("revival spell slot is exhausted") from error
    if key == "pact_magic":
        sheet.setdefault("spellcasting", {})[key] = resource
    else:
        sheet.setdefault("spellcasting", {}).setdefault("spell_slots", {})[key] = resource
    return {"kind": "spell_slot", "key": key, "level": level, **payment}


def begin_steel_defender_revival(
    owner_sheet: Mapping[str, Any],
    defender_sheet: Mapping[str, Any],
    *,
    relation: Mapping[str, Any],
    elapsed_ticks: int,
    distance_ft: int | float,
    slot_level: int,
    action_available: bool = True,
) -> dict[str, Any]:
    """Validate and pay a revival action, returning a pending ten-tick job."""

    _require_2014(owner_sheet, label="Steel Defender revival")
    _require_2014(defender_sheet, label="Steel Defender revival")
    _require_construct(defender_sheet, label="Steel Defender revival")
    if relation.get("relation_key") != STEEL_DEFENDER_RELATION_KEY:
        raise SteelDefenderError("revival requires the Steel Defender relation")
    if relation.get("status") != "dead":
        raise SteelDefenderError("revival requires a dead Steel Defender relation")
    defender_dead_at_ticks = relation.get("death_elapsed_ticks")
    if isinstance(defender_dead_at_ticks, bool) or not isinstance(defender_dead_at_ticks, int):
        raise SteelDefenderError("the Steel Defender relation requires an exact death time")
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int):
        raise SteelDefenderError("elapsed_ticks must be an integer")
    age = elapsed_ticks - defender_dead_at_ticks
    if age < 0 or age > REVIVE_WINDOW_TICKS:
        raise SteelDefenderError("the defender must have died within the last hour")
    if _distance(distance_ft) > 5:
        raise SteelDefenderError("revival requires the owner within 5 feet of the defender")
    if not _smith_tools_present(owner_sheet):
        raise SteelDefenderError("revival requires the owner's Smith's Tools")
    if not isinstance(action_available, bool) or not action_available:
        raise SteelDefenderError("revival requires the owner's action")
    if condition_ids(owner_sheet.get("conditions")) & INCAPACITATING_STATE_IDS:
        raise SteelDefenderError("an incapacitated owner cannot take the revival action")
    revival_start = relation.get("revival_started_elapsed_ticks")
    revival_complete = relation.get("revival_completes_elapsed_ticks")
    if revival_start is not None or revival_complete is not None:
        raise SteelDefenderError("the defender already has a pending revival")
    if "dead" not in condition_ids(defender_sheet.get("conditions")):
        raise SteelDefenderError("revival requires a dead defender")

    owner = deepcopy(dict(owner_sheet))
    payment = _consume_spell_slot(owner, slot_level)
    pending = {
        "status": "pending",
        "relation_key": STEEL_DEFENDER_RELATION_KEY,
        "owner_character_id": str(relation.get("owner_character_id") or ""),
        "dependent_actor_id": str(relation.get("dependent_actor_id") or ""),
        "started_elapsed_ticks": elapsed_ticks,
        "completes_elapsed_ticks": elapsed_ticks + REVIVE_DELAY_TICKS,
        "action_paid": True,
        "spell_slot": payment,
    }
    return {
        "status": "pending",
        "owner_sheet": owner,
        "defender_sheet": deepcopy(dict(defender_sheet)),
        "pending_revival": pending,
        "payment": payment,
    }


def complete_steel_defender_revival(
    defender_sheet: Mapping[str, Any],
    pending_revival: Mapping[str, Any],
    *,
    elapsed_ticks: int,
) -> dict[str, Any]:
    """Complete a due revival, or return the unchanged pending state."""

    if pending_revival.get("relation_key") != STEEL_DEFENDER_RELATION_KEY:
        raise SteelDefenderError("pending job is not a Steel Defender revival")
    if pending_revival.get("status") != "pending":
        raise SteelDefenderError("pending revival is not active")
    due = pending_revival.get("completes_elapsed_ticks")
    if isinstance(due, bool) or not isinstance(due, int):
        raise SteelDefenderError("pending revival completion tick is invalid")
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int):
        raise SteelDefenderError("elapsed_ticks must be an integer")
    if elapsed_ticks < due:
        return {
            "status": "pending",
            "sheet": deepcopy(dict(defender_sheet)),
            "pending_revival": deepcopy(dict(pending_revival)),
        }
    revived = deepcopy(dict(defender_sheet))
    combat = revived.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {}))
    maximum = int(hp.get("max", 0) or 0)
    if maximum < 1:
        raise SteelDefenderError("defender hit-point maximum must be positive")
    hp["value"] = maximum
    hp["temp"] = 0
    combat["hp"] = hp
    combat["death_saves"] = {"successes": 0, "failures": 0}
    revived["conditions"] = sorted(
        condition_ids(revived.get("conditions")) - {"dead", "unconscious", "stable"}
    )
    completed = {**dict(pending_revival), "status": "completed"}
    return {"status": "committed", "sheet": revived, "pending_revival": completed}


def kill_steel_defender_when_owner_dies(
    owner_sheet: Mapping[str, Any], defender_sheet: Mapping[str, Any],
    *, pending_revival: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply Tasha's immediate owner-death consequence to the defender."""

    _require_2014(owner_sheet, label="Steel Defender owner lifecycle")
    _require_2014(defender_sheet, label="Steel Defender owner lifecycle")
    _require_construct(defender_sheet, label="Steel Defender owner lifecycle")
    if "dead" not in condition_ids(owner_sheet.get("conditions")):
        return {
            "status": "unchanged",
            "sheet": deepcopy(dict(defender_sheet)),
            "pending_revival": deepcopy(dict(pending_revival)) if pending_revival else None,
        }
    defender = deepcopy(dict(defender_sheet))
    combat = defender.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {}))
    hp["value"] = 0
    hp["temp"] = 0
    combat["hp"] = hp
    defender["conditions"] = sorted(condition_ids(defender.get("conditions")) | {"dead"})
    return {"status": "perished", "sheet": defender, "pending_revival": None}


__all__ = [
    "REPAIR_USES_MAX",
    "REVIVE_DELAY_TICKS",
    "REVIVE_WINDOW_TICKS",
    "STEEL_DEFENDER_RELATION_KEY",
    "SteelDefenderError",
    "begin_steel_defender_revival",
    "complete_steel_defender_revival",
    "kill_steel_defender_when_owner_dies",
    "mending_steel_defender",
    "repair_steel_defender",
]
