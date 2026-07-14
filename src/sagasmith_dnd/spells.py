"""Canonical v2 spell preparation and casting-resource settlement."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sagasmith_dnd.combat_engine import CombatEngineError

_SPELL_POINT_COSTS = {1: 2, 2: 3, 3: 5, 4: 6, 5: 7, 6: 9, 7: 10, 8: 11, 9: 13}

_PREPARED_2024 = {
    "bard": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "cleric": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "druid": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "paladin": (2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15),
    "ranger": (2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15),
    "sorcerer": (2, 4, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "warlock": (2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15),
    "wizard": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 18, 19, 21, 22, 23, 24, 25),
}
_LONG_REST_ANY_2024 = {"cleric", "druid", "wizard"}
_LONG_REST_ONE_2024 = {"paladin", "ranger"}
_LEVEL_UP_ONE_2024 = {"bard", "sorcerer", "warlock"}
_PREPARED_2014 = {"cleric", "druid", "paladin", "wizard"}


def consume_spell_cast(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    cast_level: int | None = None,
    ritual: bool = False,
) -> dict[str, Any]:
    """Validate access and pay a spell's canonical slot or spell-point cost."""
    value = deepcopy(sheet)
    spell = next(
        (item for item in value.get("content", {}).get("spells", []) if item.get("id") == spell_id),
        None,
    )
    if spell is None:
        raise CombatEngineError("spell is not on this actor card")
    access = dict(spell.get("access") or {})
    mode = str(value.get("spellcasting", {}).get("preparation", {}).get("mode") or "known")
    available = bool(access.get("at_will") or access.get("always_prepared"))
    if base_level := int(spell.get("level", 0) or 0):
        if mode in {"prepared", "spellbook"}:
            available = available or bool(access.get("prepared"))
        else:
            available = available or bool(access.get("known"))
        if (
            ritual
            and access.get("ritual_available")
            and mode == "spellbook"
            and access.get("in_spellbook")
        ):
            available = True
    else:
        available = available or bool(access.get("known") or access.get("prepared"))
    if not available:
        raise CombatEngineError("spell is not available to cast")
    level = base_level if cast_level is None else int(cast_level)
    if level < base_level or level > 9:
        raise CombatEngineError("cast_level is invalid for this spell")
    spellcasting = value.setdefault("spellcasting", {})
    if ritual:
        if not access.get("ritual_available") or not spellcasting.get("ritual_casting"):
            raise CombatEngineError("spell cannot be cast as a ritual")
    paid: dict[str, Any] = {"economy": "none", "level": level, "ritual": ritual}
    if base_level > 0 and not ritual and not access.get("at_will"):
        if spellcasting.get("casting_economy", "slots") == "spell_points":
            points = spellcasting.get("spell_points")
            if not isinstance(points, dict):
                raise CombatEngineError("spell-point casting is not configured")
            cost = int(spell.get("point_cost") or _SPELL_POINT_COSTS[level])
            if int(points.get("value", 0) or 0) < cost:
                raise CombatEngineError("insufficient spell points")
            points["value"] = int(points["value"]) - cost
            paid = {"economy": "spell_points", "cost": cost, "level": level, "ritual": False}
        else:
            slots = spellcasting.get("spell_slots", {})
            slot = slots.get(str(level)) or slots.get(f"spell{level}")
            if not isinstance(slot, dict) or int(slot.get("value", 0) or 0) <= 0:
                raise CombatEngineError(f"no level {level} spell slot remains")
            slot["value"] = int(slot["value"]) - 1
            paid = {"economy": "slots", "level": level, "ritual": False}
    duration = dict(spell.get("definition", {}).get("duration") or {})
    concentration = bool(duration.get("concentration"))
    if concentration:
        for effect in value.get("effects", []):
            if effect.get("active") and effect.get("concentration"):
                effect["active"] = False
        value.setdefault("effects", []).append(
            {
                "id": f"concentration-{uuid4().hex}",
                "name": f"Concentrating: {spell.get('name') or spell_id}",
                "kind": "concentration",
                "source": "spell.cast",
                "source_spell_id": spell_id,
                "active": True,
                "concentration": True,
                "duration": {
                    "period": _duration_period(duration.get("unit")),
                    "remaining": int(duration.get("value", 0) or 0),
                },
                "changes": [],
                "description": "",
            }
        )
    return {
        "sheet": value,
        "spell_id": spell_id,
        "cast_level": level,
        "payment": paid,
        "concentration_started": concentration,
    }


def consume_readied_spell(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    cast_level: int | None = None,
) -> dict[str, Any]:
    """Cast an action spell now and replace current concentration with held energy."""
    spell = next(
        (item for item in sheet.get("content", {}).get("spells", []) if item.get("id") == spell_id),
        None,
    )
    if spell is None:
        raise CombatEngineError("spell is not on this actor card")
    casting_time = str(spell.get("definition", {}).get("casting_time") or "")
    normalized_casting_time = casting_time.casefold().strip()
    if not (normalized_casting_time == "action" or normalized_casting_time.startswith("1 action")):
        raise CombatEngineError("only a spell with a casting time of one action can be readied")

    applied = consume_spell_cast(
        sheet,
        spell_id=spell_id,
        cast_level=cast_level,
        ritual=False,
    )
    value = applied["sheet"]
    for effect in value.get("effects", []):
        if effect.get("active") and effect.get("concentration"):
            effect["active"] = False

    duration = dict(spell.get("definition", {}).get("duration") or {})
    release_concentration = bool(duration.get("concentration"))
    holding_effect = None
    if release_concentration:
        candidates = [
            effect
            for effect in value.get("effects", [])
            if effect.get("source_spell_id") == spell_id and effect.get("concentration")
        ]
        if candidates:
            holding_effect = candidates[-1]
            holding_effect["active"] = True
    if holding_effect is None:
        holding_effect = {
            "id": f"readied-spell-{uuid4().hex}",
            "name": f"Holding: {spell.get('name') or spell_id}",
            "kind": "readied_spell",
            "source": "spell.ready",
            "source_spell_id": spell_id,
            "active": True,
            "concentration": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "description": "",
        }
        value.setdefault("effects", []).append(holding_effect)
    release_duration = dict(holding_effect.get("duration") or {})
    release_kind = str(holding_effect.get("kind") or "concentration")
    holding_effect["duration"] = {"period": "manual", "remaining": 0}
    holding_effect["kind"] = "readied_spell"
    holding_effect["source"] = "spell.ready"
    return {
        **{key: item for key, item in applied.items() if key != "sheet"},
        "sheet": value,
        "casting_time": normalized_casting_time,
        "holding_effect_id": holding_effect["id"],
        "release_concentration": release_concentration,
        "release_duration": release_duration,
        "release_effect_kind": release_kind,
    }


def replace_prepared_spells(
    sheet: dict[str, Any], *, spell_ids: list[str], event: str
) -> dict[str, Any]:
    """Replace a complete prepared list under the 2014/2024 class rules."""
    value = deepcopy(sheet)
    preparation = value.get("spellcasting", {}).get("preparation", {})
    if preparation.get("mode") not in {"prepared", "spellbook"}:
        raise CombatEngineError("this character does not prepare level 1+ spells")
    normalized_event = str(event).strip().lower().replace("-", "_")
    if normalized_event not in {"setup", "long_rest", "level_up"}:
        raise CombatEngineError("preparation event must be setup, long_rest, or level_up")
    selected = [str(item).strip() for item in spell_ids]
    if any(not item for item in selected) or len(selected) != len(set(selected)):
        raise CombatEngineError("prepared spell ids must be non-empty and unique")

    spells = {str(item.get("id")): item for item in value.get("content", {}).get("spells", [])}
    missing = [item for item in selected if item not in spells]
    if missing:
        raise CombatEngineError(f"prepared spell is not on this actor card: {missing[0]}")
    classes = {
        _class_key(item.get("name")): int(item.get("level", 0) or 0)
        for item in value.get("progression", {}).get("classes", [])
    }
    if not classes:
        raise CombatEngineError("prepared spell rules require at least one recorded class")
    edition = _edition(value)

    def source_for(spell: dict[str, Any]) -> str:
        raw = str(spell.get("grant", {}).get("source_key") or "")
        source = _class_key(raw)
        if source in classes:
            return source
        if len(classes) == 1:
            return next(iter(classes))
        raise CombatEngineError(
            f"multiclass spell {spell.get('id')} needs grant.source_key identifying its class"
        )

    old_ids = list(preparation.get("selected_spell_ids") or [])
    relevant_ids = set(old_ids) | set(selected)
    by_source_old: dict[str, set[str]] = {}
    by_source_new: dict[str, set[str]] = {}
    for spell_id in relevant_ids:
        spell = spells.get(spell_id)
        if spell is None:
            raise CombatEngineError(f"prepared spell is not on this actor card: {spell_id}")
        if int(spell.get("level", 0) or 0) == 0:
            raise CombatEngineError("cantrips are known, not selected as prepared level 1+ spells")
        if spell.get("access", {}).get("always_prepared"):
            raise CombatEngineError("always-prepared spells must not count in the selected list")
        source = source_for(spell)
        if spell_id in old_ids:
            by_source_old.setdefault(source, set()).add(spell_id)
        if spell_id in selected:
            spellbook_ids = set(
                value.get("spellcasting", {}).get("spellbook", {}).get("spell_ids") or []
            )
            if preparation.get("mode") == "spellbook" and spell_id not in spellbook_ids:
                raise CombatEngineError("a wizard can prepare only spells in their spellbook")
            maximum_level = _maximum_spell_level(edition, source, classes[source])
            if int(spell.get("level", 0) or 0) > maximum_level:
                raise CombatEngineError(
                    f"{source} level {classes[source]} cannot prepare spell level "
                    f"{spell.get('level')}"
                )
            by_source_new.setdefault(source, set()).add(spell_id)

    source_limits: dict[str, int] = {}
    for source, chosen in by_source_new.items():
        limit = _prepared_limit(value, edition, source, classes[source])
        source_limits[source] = limit
        if len(chosen) > limit:
            raise CombatEngineError(f"{source} prepared spell selection exceeds {limit}")

    changed_sources = {
        source
        for source in set(by_source_old) | set(by_source_new)
        if by_source_old.get(source, set()) != by_source_new.get(source, set())
    }
    for source in changed_sources:
        old = by_source_old.get(source, set())
        new = by_source_new.get(source, set())
        removed = old - new
        added = new - old
        if normalized_event == "long_rest":
            maximum_replacements = _long_rest_replacements(edition, source)
            if maximum_replacements == 0:
                raise CombatEngineError(f"{source} cannot change prepared spells on a long rest")
            if len(old) != len(new):
                raise CombatEngineError(
                    "a long-rest change replaces spells; additions belong to setup or level up"
                )
            if maximum_replacements is not None and (
                len(removed) > maximum_replacements or len(added) > maximum_replacements
            ):
                raise CombatEngineError(
                    f"{source} can replace only {maximum_replacements} spell per long rest"
                )
        elif normalized_event == "level_up":
            maximum_replacements = _level_up_replacements(edition, source)
            if len(new) < len(old):
                raise CombatEngineError(
                    "level-up preparation may add newly available spells or replace a legal "
                    "entry, but cannot shrink the prepared list"
                )
            if removed and maximum_replacements == 0:
                raise CombatEngineError(
                    f"{source} can add newly available preparations but cannot replace them "
                    "on level up"
                )
            if maximum_replacements is not None and len(removed) > maximum_replacements:
                raise CombatEngineError(
                    f"{source} can replace only {maximum_replacements} spell per level gained"
                )

    preparation["selected_spell_ids"] = selected
    if source_limits and len(source_limits) == len(classes):
        preparation["max_prepared"] = sum(source_limits.values())
    for spell in spells.values():
        spell.setdefault("access", {})["prepared"] = bool(
            spell.get("access", {}).get("always_prepared") or spell.get("id") in selected
        )
    return {
        "sheet": value,
        "event": normalized_event,
        "selected_spell_ids": selected,
        "added": sorted(set(selected) - set(old_ids)),
        "removed": sorted(set(old_ids) - set(selected)),
        "limits": source_limits,
    }


def _class_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    for prefix in ("class:", "class/", "class-"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.split(":")[-1].split("/")[-1]


def _edition(sheet: dict[str, Any]) -> str:
    text = str(sheet.get("edition") or "2014").lower()
    return "2024" if "2024" in text or "5.2" in text else "2014"


def _prepared_limit(sheet: dict[str, Any], edition: str, source: str, level: int) -> int:
    if edition == "2024" and source in _PREPARED_2024:
        return _PREPARED_2024[source][level - 1]
    if edition == "2014" and source in _PREPARED_2014:
        ability_name = {
            "cleric": "wisdom",
            "druid": "wisdom",
            "paladin": "charisma",
            "wizard": "intelligence",
        }[source]
        score = int(sheet.get("abilities", {}).get(ability_name, {}).get("score", 10) or 10)
        modifier = (score - 10) // 2
        class_levels = level // 2 if source == "paladin" else level
        return max(1, class_levels + modifier)
    if edition == "2014" and source in {"bard", "ranger", "sorcerer", "warlock"}:
        raise CombatEngineError(f"2014 {source} uses spells known, not prepared spells")
    return int(sheet.get("spellcasting", {}).get("preparation", {}).get("max_prepared", 0) or 0)


def _maximum_spell_level(edition: str, source: str, level: int) -> int:
    if source in {"paladin", "ranger"}:
        if edition == "2024":
            return min(5, ((level - 1) // 4) + 1)
        return min(5, (level + 3) // 4) if level >= 2 else 0
    if source == "warlock":
        return min(5, ((level + 1) // 2))
    return min(9, (level + 1) // 2)


def _long_rest_replacements(edition: str, source: str) -> int | None:
    if edition == "2024":
        if source in _LONG_REST_ANY_2024:
            return None
        if source in _LONG_REST_ONE_2024:
            return 1
        return 0
    return None if source in _PREPARED_2014 else 0


def _level_up_replacements(edition: str, source: str) -> int | None:
    if edition == "2024" and source in _LEVEL_UP_ONE_2024:
        return 1
    return 0


def _duration_period(unit: Any) -> str:
    return {"round": "round", "minute": "minute", "hour": "hour", "day": "day"}.get(
        str(unit or ""), "manual"
    )
