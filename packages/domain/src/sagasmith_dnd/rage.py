"""Source-bound 2014 Rage and the activity since the actor's last turn."""

from copy import deepcopy

FEATURE = "dnd5e.content.srd2014.feature.barbarian-rage"
PERSISTENT = "dnd5e.content.srd2014.feature.barbarian-persistent-rage"
MECHANIC = "dnd5e.core.class.rage"
KIND = "rage_2014"
ACTIVITY = "rage_turn_activity_2014"


def feature(sheet, identifier=FEATURE):
    if sheet.get("edition") != "2014":
        return None
    return next(
        (
            f
            for f in sheet.get("content", {}).get("features", [])
            if f.get("id") == identifier
            and f.get("pack_id") == "dnd5e.content.srd2014"
            and MECHANIC in f.get("mechanic_refs", [])
            and f.get("rule_refs")
        ),
        None,
    )


def level(sheet):
    return sum(
        int(c.get("level", 0))
        for c in sheet.get("progression", {}).get("classes", [])
        if c.get("name", "").casefold() == "barbarian"
    )


def active(sheet):
    if not feature(sheet):
        return None
    effects = [e for e in sheet.get("effects", []) if e.get("active") and e.get("kind") == KIND]
    if len(effects) > 1:
        raise ValueError("a creature cannot have multiple active Rages")
    if not effects:
        return None
    effect = effects[0]
    if effect.get("source") != FEATURE or effect.get("metadata", {}).get("mechanic_id") != MECHANIC:
        raise ValueError("Rage requires its reviewed source activation")
    return effect


def benefits(sheet):
    from .conditions import condition_ids

    if not active(sheet) or condition_ids(sheet.get("conditions")) & {"unconscious", "dead"}:
        return False
    inventory = sheet.get("inventory", {})
    armor = inventory.get("equipment_slots", {}).get("armor")
    return not any(
        i.get("id") == armor and i.get("mechanics", {}).get("category") == "heavy"
        for i in inventory.get("items", [])
    )


def damage_bonus(sheet, *, attack_ability, attack_mode):
    if attack_ability != "strength" or attack_mode != "melee" or not benefits(sheet):
        return 0
    return 4 if level(sheet) >= 16 else 3 if level(sheet) >= 9 else 2


def require_spell_allowed(sheet):
    if active(sheet):
        from .combat_engine import CombatEngineError

        raise CombatEngineError("Rage prevents casting or concentrating on spells")


def end(sheet, reason):
    ended = []
    for effect in sheet.get("effects", []):
        if effect.get("active") and effect.get("kind") == KIND:
            effect.update(active=False, ended_reason=reason)
            ended.append(effect["id"])
    return ended


def note_activity(sheet, *, attacked=False, damaged=False):
    if not feature(sheet):
        return
    entry = next((e for e in sheet.get("effects", []) if e.get("kind") == ACTIVITY), None)
    if entry is None:
        entry = {
            "id": ACTIVITY,
            "name": "Activity since last turn",
            "kind": ACTIVITY,
            "source": FEATURE,
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "metadata": {"attacked_hostile": False, "took_damage": False},
        }
        sheet.setdefault("effects", []).append(entry)
    if attacked:
        entry["metadata"]["attacked_hostile"] = True
    if damaged:
        entry["metadata"]["took_damage"] = True


def reset_activity(sheet):
    for e in sheet.get("effects", []):
        if e.get("kind") == ACTIVITY:
            e["metadata"] = {"attacked_hostile": False, "took_damage": False}


def end_turn(sheet):
    value = deepcopy(sheet)
    activity = next(
        (e["metadata"] for e in value.get("effects", []) if e.get("kind") == ACTIVITY), {}
    )
    ended = []
    if active(value) and not (level(value) >= 15 and feature(value, PERSISTENT)):
        if not activity.get("attacked_hostile") and not activity.get("took_damage"):
            ended = end(value, "no_hostile_attack_or_damage")
    reset_activity(value)
    return {"sheet": value, "ended": ended, "activity": deepcopy(activity)}


def enter(sheet, effect_id):
    from .character_schema import add_effect
    from .conditions import condition_ids
    from .spells import end_concentration_effects

    if not feature(sheet) or not 1 <= level(sheet) <= 20:
        raise ValueError("Rage requires the reviewed 2014 Barbarian feature and class level")
    if active(sheet):
        raise ValueError("Rage is already active")
    if (
        condition_ids(sheet.get("conditions"))
        & {"unconscious", "dead", "incapacitated", "paralyzed", "stunned", "petrified"}
        or sheet.get("combat", {}).get("hp", {}).get("value", 0) <= 0
    ):
        raise ValueError("Rage cannot start under the actor's current conditions")
    ended = end_concentration_effects(
        sheet,
        effect_ids=[
            e["id"] for e in sheet.get("effects", []) if e.get("active") and e.get("concentration")
        ],
        ended_reason="rage",
    )
    effect = {
        "id": effect_id,
        "name": "Rage",
        "kind": KIND,
        "source": FEATURE,
        "active": True,
        "concentration": False,
        "duration": {"period": "minute", "remaining": 1},
        "changes": [],
        "metadata": {"mechanic_id": MECHANIC, "rule_refs": deepcopy(feature(sheet)["rule_refs"])},
    }
    value, _ = add_effect(ended["sheet"], effect)
    return {
        "sheet": value,
        "effect": active(value),
        "ended_concentration_effect_ids": ended["ended_effect_ids"],
    }
