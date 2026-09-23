"""2014 Divine Smite: an owned choice after a weapon hit, before damage."""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from .legendary_resistance import SaveDecisionRequiredError

FEATURE = "dnd5e.content.srd2014.feature.paladin-divine-smite"
MECHANIC = "dnd5e.core.class.divine_smite"
_HANDLER = ContextVar("dnd_smite_handler", default=None)


def feature(sheet):
    level = sum(
        int(c.get("level", 0))
        for c in sheet.get("progression", {}).get("classes", [])
        if c.get("name", "").casefold() == "paladin"
    )
    if sheet.get("edition") != "2014" or level < 2:
        return None
    return next(
        (
            f
            for f in sheet.get("content", {}).get("features", [])
            if f.get("id") == FEATURE
            and f.get("pack_id") == "dnd5e.content.srd2014"
            and MECHANIC in f.get("mechanic_refs", [])
            and f.get("rule_refs")
        ),
        None,
    )


def slots(sheet):
    casting = sheet.get("spellcasting", {})
    values = []
    for key, resource in casting.get("spell_slots", {}).items():
        match = re.fullmatch(r"(?:level_)?([1-9])", str(key))
        if match and int(resource.get("value", 0)) > 0:
            values.append({"slot": str(key), "level": int(match[1])})
    pact = casting.get("pact_magic") or {}
    if int(pact.get("value", 0)) > 0 and 1 <= int(pact.get("slot_level", 0)) <= 9:
        values.append({"slot": "pact_magic", "level": int(pact["slot_level"])})
    return sorted(values, key=lambda v: (v["level"], v["slot"]))


def settle(actor_id, sheet, target_id, target_sheet, plan, attack):
    entry = feature(sheet)
    if (
        not entry
        or not attack.get("hit")
        or plan.get("kind") != "attack"
        or not plan.get("melee_attack")
        or plan.get("unarmed_strike")
        or plan.get("attack_ability") == "spell"
        or not plan.get("damage_expression")
        or not slots(sheet)
    ):
        return None
    result = {
        "kind": "divine_smite",
        "target_id": target_id,
        "weapon_id": plan.get("weapon_id"),
        "hit": True,
        "critical": bool(attack.get("critical")),
        "slots": slots(sheet),
    }
    handler = _HANDLER.get()
    if handler is None:
        raise SaveDecisionRequiredError(actor_id, sheet, result)
    selected = handler(actor_id, sheet, result, entry)
    if selected is None:
        return None
    option = next((v for v in slots(sheet) if v["slot"] == selected), None)
    if option is None:
        raise ValueError("Divine Smite requires an available selected spell slot")
    casting = sheet["spellcasting"]
    pool = casting["pact_magic"] if selected == "pact_magic" else casting["spell_slots"][selected]
    pool["value"] -= 1
    species = str(target_sheet.get("progression", {}).get("species") or "").strip()
    extra = bool(re.fullmatch(r"(?:undead|fiend)(?:\s*\([^()]+\))?", species, re.I))
    count = min(5, option["level"] + 1) + int(extra)
    return {
        "damage_expression": f"{count}d8",
        "damage_type": "radiant",
        "source": FEATURE,
        "slot": selected,
        "slot_level": option["level"],
        "creature_type_bonus": extra,
        "mechanic_id": MECHANIC,
        "rule_refs": deepcopy(entry["rule_refs"]),
    }


@contextmanager
def decisions(handler):
    token = _HANDLER.set(handler)
    try:
        yield
    finally:
        _HANDLER.reset(token)
