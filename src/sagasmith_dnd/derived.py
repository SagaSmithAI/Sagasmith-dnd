"""Prepare D&D Actor derived data from system, items, and active effects."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.rolls import SKILL_ABILITIES


def prepare_actor_derived(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    effective = deepcopy(actor.system or {})
    _prepare_proficiency(effective)
    item_summary = _apply_equipped_items(documents, campaign_id, actor_id, effective)
    effect_summary = _apply_effects(documents, campaign_id, actor_id, effective)
    roll_summary = _prepare_roll_data(effective)
    derived = {
        **dict(actor.derived or {}),
        "effective_system": effective,
        "items": item_summary,
        "effects": effect_summary,
        "rolls": roll_summary,
        "statuses": effect_summary["statuses"],
    }
    updated = documents.update_actor(actor_id, derived=derived)
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="actor_prepare",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        deltas=[{"type": "actor_prepare", "actor_id": actor_id, "derived": derived}],
        narration_hints=[f"{actor.name}'s derived data is prepared."],
    )
    return {"actor": asdict(updated), "derived": derived, "messages": [asdict(message)]}


def _prepare_proficiency(system: dict[str, Any]) -> None:
    level = int(system.get("level") or system.get("details", {}).get("level") or 1)
    proficiency = int(
        system.get("attributes", {}).get("prof")
        or (2 + (max(1, level) - 1) // 4)
    )
    system.setdefault("attributes", {})["prof"] = proficiency


def _prepare_roll_data(system: dict[str, Any]) -> dict[str, Any]:
    prof = int(system.setdefault("attributes", {}).get("prof", 2) or 2)
    abilities = system.setdefault("abilities", {})
    ability_summary = {}
    for ability in ("str", "dex", "con", "int", "wis", "cha"):
        data = abilities.setdefault(ability, {"value": 10})
        if not isinstance(data, dict):
            data = {"value": int(data or 10)}
            abilities[ability] = data
        score = int(data.get("value", 10) or 10)
        modifier = (score - 10) // 2
        save_prof = 1 if data.get("save_proficient") or data.get("proficient") else int(data.get("saveProf", 0) or 0)
        data["mod"] = modifier
        data["save"] = modifier + (prof * save_prof)
        ability_summary[ability] = {"value": score, "mod": modifier, "save": data["save"]}

    skills = system.setdefault("skills", {})
    skill_summary = {}
    for skill, ability in SKILL_ABILITIES.items():
        data = skills.setdefault(skill, {})
        if not isinstance(data, dict):
            data = {"prof": int(data or 0)}
            skills[skill] = data
        multiplier = _skill_multiplier(data)
        modifier = int(abilities[ability]["mod"]) + (prof * multiplier)
        data["ability"] = ability
        data["mod"] = modifier
        data["passive"] = 10 + modifier
        skill_summary[skill] = {"ability": ability, "mod": modifier, "passive": data["passive"]}
    return {"abilities": ability_summary, "skills": skill_summary}


def _skill_multiplier(data: dict[str, Any]) -> int:
    if data.get("expertise"):
        return 2
    if data.get("proficient"):
        return 1
    return int(data.get("prof", 0) or 0)


def _apply_equipped_items(
    documents: FoundryDocumentService,
    campaign_id: str,
    actor_id: str,
    effective: dict[str, Any],
) -> dict[str, Any]:
    equipped = []
    transferred_effects = []
    ac_candidates = []
    for item in documents.list_items(campaign_id, actor_id=actor_id):
        system = dict(item.system or {})
        if not bool(system.get("equipped")):
            continue
        equipped.append(item.id)
        armor = system.get("armor")
        if isinstance(armor, dict):
            value = armor.get("value") or armor.get("ac")
            if value is not None:
                ac_candidates.append(int(value))
        if system.get("ac_bonus") is not None:
            _add_path(effective, ["attributes", "ac", "bonus"], int(system.get("ac_bonus") or 0))
        for trait_key in ("dr", "di", "dv", "ci"):
            values = system.get("traits", {}).get(trait_key)
            if values:
                _merge_trait(effective, trait_key, values)
        for effect in _iter_item_transfer_effects(item):
            for change in _sorted_changes(effect.get("changes") or []):
                _apply_change(effective, dict(change))
            transferred_effects.append(
                effect.get("_id") or effect.get("id") or effect.get("name") or item.id
            )
    if ac_candidates:
        ac = effective.setdefault("attributes", {}).setdefault("ac", {})
        if isinstance(ac, dict):
            ac["value"] = max([int(ac.get("value", 10) or 10), *ac_candidates])
    return {"equipped": equipped, "transferred_effects": transferred_effects}


def _apply_effects(
    documents: FoundryDocumentService,
    campaign_id: str,
    actor_id: str,
    effective: dict[str, Any],
) -> dict[str, Any]:
    applied = []
    statuses = set()
    for effect in documents.list_effects(campaign_id, actor_id=actor_id):
        if effect.disabled or effect.suppressed:
            continue
        for change in _sorted_changes(effect.changes):
            _apply_change(effective, dict(change))
        statuses.update(effect.statuses)
        applied.append(effect.id)
    return {"applied": applied, "statuses": sorted(statuses)}


def _apply_change(target: dict[str, Any], change: dict[str, Any]) -> None:
    path = str(change.get("key") or "").strip()
    if not path:
        return
    if path.startswith("system."):
        path = path.removeprefix("system.")
    parts = path.split(".")
    parent = target
    for part in parts[:-1]:
        child = parent.get(part)
        if not isinstance(child, dict):
            child = {}
            parent[part] = child
        parent = child
    key = parts[-1]
    mode = _mode_name(change.get("mode"))
    value = change.get("value")
    if mode == "OVERRIDE":
        parent[key] = value
    elif mode == "MULTIPLY":
        parent[key] = _number(parent.get(key), 1) * _number(value, 1)
    elif mode == "UPGRADE":
        parent[key] = max(_number(parent.get(key)), _number(value))
    elif mode == "DOWNGRADE":
        parent[key] = min(_number(parent.get(key)), _number(value))
    else:
        parent[key] = _number(parent.get(key)) + _number(value) if _is_number_like(value) else value


def _sorted_changes(changes: list[Any]) -> list[dict[str, Any]]:
    values = [dict(change) for change in changes if isinstance(change, dict)]
    return sorted(values, key=lambda item: int(item.get("priority", _mode_priority(item.get("mode"))) or 0))


def _mode_name(mode: Any) -> str:
    if isinstance(mode, int) or (isinstance(mode, str) and mode.isdigit()):
        return {
            0: "OVERRIDE",
            1: "MULTIPLY",
            2: "ADD",
            3: "DOWNGRADE",
            4: "UPGRADE",
            5: "OVERRIDE",
        }.get(int(mode), "ADD")
    return str(mode or "ADD").upper()


def _mode_priority(mode: Any) -> int:
    if isinstance(mode, int) or (isinstance(mode, str) and mode.isdigit()):
        return int(mode) * 10
    return {
        "MULTIPLY": 10,
        "ADD": 20,
        "DOWNGRADE": 30,
        "UPGRADE": 40,
        "OVERRIDE": 50,
    }.get(str(mode or "ADD").upper(), 20)


def _iter_item_transfer_effects(item: Any) -> list[dict[str, Any]]:
    system = dict(item.system or {})
    attunement_required = system.get("attunement") == "required"
    if system.get("hidden") or (attunement_required and not system.get("attuned")):
        return []
    effects = []
    for raw in item.effects or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("disabled") or raw.get("suppressed"):
            continue
        if raw.get("transfer") is False:
            continue
        effects.append(raw)
    return effects


def _add_path(target: dict[str, Any], parts: list[str], amount: int) -> None:
    parent = target
    for part in parts[:-1]:
        child = parent.get(part)
        if not isinstance(child, dict):
            child = {}
            parent[part] = child
        parent = child
    parent[parts[-1]] = int(parent.get(parts[-1], 0) or 0) + amount


def _merge_trait(target: dict[str, Any], trait_key: str, values: Any) -> None:
    traits = target.setdefault("traits", {})
    trait = traits.setdefault(trait_key, {"value": []})
    current = set(trait.get("value") or [])
    if isinstance(values, dict):
        raw = values.get("value") or []
    elif isinstance(values, list):
        raw = values
    else:
        raw = [values]
    trait["value"] = sorted({*current, *(str(item) for item in raw)})


def _is_number_like(value: Any) -> bool:
    try:
        _number(value)
    except (TypeError, ValueError):
        return False
    return True


def _number(value: Any, default: int = 0) -> int | float:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        return float(text)
