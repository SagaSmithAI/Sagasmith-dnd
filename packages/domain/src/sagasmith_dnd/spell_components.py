"""2014 component eligibility from reviewed cards, inventory and scene effects.

Names and narrative text never confer a focus or waive a component. A portable
item's ``mechanics.spell_component`` binds its reviewed role. Temporary silence
and bound hands use active, source-bound effect metadata, so expiration and
concentration reconciliation use the ordinary effect lifecycle.
"""

from __future__ import annotations

from typing import Any

FOCUS_CLASSES = {
    "arcane": {"sorcerer", "warlock", "wizard"},
    "druidic": {"druid"},
    "holy_symbol": {"cleric", "paladin"},
    "musical_instrument": {"bard"},
}


def normalize_component_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "kind",
        "source",
        "focus_type",
        "spell_ids",
        "presentation",
    }:
        raise ValueError("spell_component requires a reviewed item contract")
    kind = value.get("kind")
    if kind not in {"pouch", "focus", "material"}:
        raise ValueError("spell_component kind must be pouch, focus, or material")
    source = value.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("spell_component requires source evidence")
    result = {"kind": kind, "source": source.strip()}
    if kind == "focus":
        focus_type = value.get("focus_type")
        if focus_type not in FOCUS_CLASSES:
            raise ValueError("spell_component focus_type is not a supported 2014 focus")
        presentation = value.get("presentation", "held")
        if presentation not in {"held", "worn", "shield"} or (
            presentation != "held" and focus_type != "holy_symbol"
        ):
            raise ValueError("only a holy symbol supports worn or shield presentation")
        if "spell_ids" in value:
            raise ValueError("a focus cannot replace specific costly or consumed materials")
        result.update(focus_type=focus_type, presentation=presentation)
    elif kind == "material":
        spell_ids = value.get("spell_ids")
        if (
            not isinstance(spell_ids, list)
            or not spell_ids
            or any(not isinstance(item, str) or not item.strip() for item in spell_ids)
            or len(set(spell_ids)) != len(spell_ids)
        ):
            raise ValueError("material spell_ids must bind the exact reviewed spells")
        if "focus_type" in value or "presentation" in value:
            raise ValueError("a material cannot claim a focus presentation")
        result["spell_ids"] = list(spell_ids)
    elif set(value) - {"kind", "source"}:
        raise ValueError("a component pouch cannot claim a focus or specific material")
    return result


def normalize_component_constraints(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not value
        or set(value)
        - {
            "can_speak",
            "usable_hands",
        }
    ):
        raise ValueError("spell_component_constraints require can_speak or usable_hands")
    if "can_speak" in value and not isinstance(value["can_speak"], bool):
        raise ValueError("spell_component_constraints.can_speak must be boolean")
    hands = value.get("usable_hands", 2)
    if isinstance(hands, bool) or not isinstance(hands, int) or not 0 <= hands <= 2:
        raise ValueError("spell_component_constraints.usable_hands must be 0, 1, or 2")
    return dict(value)


def _focus_class(sheet: dict[str, Any], spell: dict[str, Any]) -> str:
    # Spell grant, not the union of a multiclass actor's lists, owns focus access.
    grant = dict(spell.get("grant") or {})
    if grant.get("source_type") == "class":
        from .spells import _class_key

        return _class_key(grant.get("source_key"))
    return ""


def check_components(
    sheet: dict[str, Any],
    spell: dict[str, Any],
    components: dict[str, Any],
    *,
    ruling: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only eligibility and exact material payment proposal, before any RNG."""
    from .combat_engine import CombatEngineError, NeedsRulingError

    if str(sheet.get("edition") or "2014") != "2014":
        return {"edition": sheet.get("edition"), "status": "outside_2014_contract"}
    data = dict(ruling or {})
    unknown = set(data) - {
        "material_item_id",
        "casting_perception",
        "source_components_confirmed",
        "source_components",
        "source",
        "material_confirmed",
    }
    if unknown:
        raise CombatEngineError(
            "unsupported component_ruling fields: " + ", ".join(sorted(unknown))
        )
    required = dict(components)
    source_unknown = (
        dict(spell.get("custom_definition") or {}).get("component_details")
        == "not_repeated_in_statblock"
    )
    if source_unknown and not (overrides or {}).get("ignore_components"):
        reviewed = data.get("source_components")
        if (
            not isinstance(reviewed, dict)
            or not all(
                isinstance(reviewed.get(key), bool) for key in ("verbal", "somatic", "material")
            )
            or not str(data.get("source") or "").strip()
        ):
            raise NeedsRulingError(
                "source_components_confirmed alone cannot establish V/S/M; "
                "provide reviewed source_components and source",
                missing=("source_components",),
                ruling_kind="missing_or_conflicting_source_review",
            )
        from .character_schema import normalize_spell_definition

        required = normalize_spell_definition({"components": reviewed})["components"]
    if (overrides or {}).get("ignore_components") is True:
        required.update(
            verbal=False, somatic=False, material=False, material_cost_cp=0, consumed=False
        )
    elif (overrides or {}).get("ignore_material_components") is True:
        required.update(material=False, material_cost_cp=0, consumed=False)
    if not required.get("material") and (
        required.get("consumed") or int(required.get("material_cost_cp", 0)) > 0
    ):
        raise CombatEngineError("component source records cost/consumption without a material")
    hands = int(sheet.get("traits", {}).get("anatomy", {}).get("functional_hands", 2))
    can_speak = True
    constraints = []
    for effect in sheet.get("effects", []):
        facts = dict(effect.get("metadata") or {}).get("spell_component_constraints")
        if not effect.get("active") or facts is None:
            continue
        if not str(effect.get("source") or "").strip():
            raise CombatEngineError("component constraint effect requires source evidence")
        facts = normalize_component_constraints(facts)
        can_speak = can_speak and facts.get("can_speak", True)
        hands = min(hands, facts.get("usable_hands", hands))
        constraints.append(str(effect.get("id") or ""))
    if required.get("verbal") and not can_speak:
        raise CombatEngineError("verbal component is prevented by the current source-bound effect")
    slots = dict(sheet.get("inventory", {}).get("equipment_slots") or {})
    held = {str(slots[key]) for key in ("main_hand", "off_hand", "shield") if slots.get(key)}
    free_hands = max(0, hands - len(held))
    selected = None
    shared_hand = False
    if required.get("material"):
        specific = bool(required.get("consumed") or int(required.get("material_cost_cp", 0)))
        requested = data.get("material_item_id")
        if requested is not None and (not isinstance(requested, str) or not requested):
            raise CombatEngineError("material_item_id must identify an inventory item")
        for item in sheet.get("inventory", {}).get("items", []):
            if requested and item.get("id") != requested:
                continue
            raw = dict(item.get("mechanics") or {}).get("spell_component")
            if raw is None or item.get("condition", "normal") != "normal":
                continue
            contract = normalize_component_item(raw)
            kind = contract["kind"]
            if int(item.get("quantity", 0)) < 1:
                continue
            if kind == "material":
                if spell.get("id") not in contract["spell_ids"] or int(
                    item.get("price_cp", 0)
                ) < int(required.get("material_cost_cp", 0)):
                    continue
            elif specific:
                continue
            elif (
                kind == "focus"
                and _focus_class(sheet, spell) not in FOCUS_CLASSES[contract["focus_type"]]
            ):
                continue
            presentation = contract.get("presentation", "held")
            holding = str(item.get("id")) in held and hands >= len(held)
            if kind == "focus" and presentation == "worn":
                if not item.get("equipped") or item.get("equipped_slot") in {
                    None,
                    "main_hand",
                    "off_hand",
                    "shield",
                }:
                    continue
                shares = False
            elif kind == "focus" and presentation == "shield":
                if item.get("id") != slots.get("shield") or not holding:
                    continue
                shares = True
            else:
                if kind == "pouch" and free_hands < 1:
                    continue
                if not holding and free_hands < 1:
                    continue
                # A focus must already be held. Accessing a pouch/material uses
                # the available hand; it does not silently stow an equipped item.
                if kind == "focus" and not holding:
                    continue
                shares = True
            if required.get("somatic") and free_hands < 1 and not shares:
                continue
            if (
                required.get("consumed")
                and int(item.get("quantity", 0)) == 1
                and any(
                    child.get("container_id") == item["id"]
                    for child in sheet.get("inventory", {}).get("items", [])
                )
            ):
                raise CombatEngineError("a consumed material cannot contain other inventory")
            selected = {
                "item_id": item["id"],
                "kind": kind,
                "source": contract["source"],
                "consumed": bool(required.get("consumed")),
                "quantity": 1,
            }
            shared_hand = shares
            break
        if selected is None:
            raise NeedsRulingError(
                "material component needs an accessible, reviewed inventory material "
                "or legal held focus/component pouch; material_confirmed cannot replace it",
                missing=("material_component",),
                ruling_kind="source_or_scene_fact",
            )
    if required.get("somatic") and free_hands < 1 and not shared_hand:
        raise CombatEngineError("somatic component requires a usable free hand")
    return {
        "edition": "2014",
        "status": "satisfied",
        "required": required,
        "constraint_effect_ids": constraints,
        "free_hands": free_hands,
        "shared_material_hand": shared_hand,
        "material": selected,
    }


def pay_components(sheet: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Consume the previously validated material in the caller's atomic cast."""
    material = receipt.get("material")
    if not material or not material.get("consumed"):
        return sheet
    from .character_schema import remove_inventory_item

    return remove_inventory_item(sheet, material["item_id"], quantity=material["quantity"])[0]


def preflight_spell_components(
    sheet: dict[str, Any],
    spell: dict[str, Any],
    *,
    component_ruling: dict[str, Any] | None = None,
    feature_cast_source: str | None = None,
    item_componentless: bool = False,
) -> dict[str, Any]:
    """Read the same source selection as payment, without advancing time or RNG."""
    sources = list(dict(spell.get("access") or {}).get("feature_casting_sources") or [])
    selected = next(
        (
            item
            for item in sources
            if feature_cast_source
            and str(item.get("source_key") or "").casefold() == feature_cast_source.casefold()
        ),
        None,
    )
    if not feature_cast_source and len(sources) == 1 and not sources[0].get("allow_slot_cast"):
        selected = sources[0]
    overrides = dict((selected or {}).get("casting_overrides") or {})
    if item_componentless:
        overrides = {"ignore_components": True}
    return check_components(
        sheet,
        spell,
        dict(spell.get("definition", {}).get("components") or {}),
        ruling=component_ruling,
        overrides=overrides,
    )
