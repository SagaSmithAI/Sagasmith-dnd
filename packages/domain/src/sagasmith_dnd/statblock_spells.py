"""Deterministic hydration of parsed statblock spellcasting into character cards."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Sequence

from sagasmith_core.text import ascii_slug

from sagasmith_dnd.character_schema import validate_character_sheet
from sagasmith_dnd.rule_engine import (
    EXTERNAL_RULING_KIND_ORDER,
    EXTERNAL_RULING_KINDS,
    RULING_KINDS,
)
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    overlay_spell_attack_card,
    spell_attack_action_resolution,
)

ContentArtifact = tuple[str, str, dict[str, Any]]


def _ruling_requirement(reason: str, ruling_kind: str) -> dict[str, Any]:
    if ruling_kind not in RULING_KINDS:
        ruling_kind = "agent_dm_adjudication"
    if ruling_kind in EXTERNAL_RULING_KINDS:
        resolution = {
            "default_resolver": "external_input",
            "ruling_kind": ruling_kind,
            "policy_ref": "server_capabilities.ruling_policy",
        }
    else:
        resolution = {
            "default_resolver": "agent",
            "ruling_kind": ruling_kind,
            "policy_ref": "server_capabilities.ruling_policy",
            "requires_external_input_only_for": list(EXTERNAL_RULING_KIND_ORDER),
        }
    return {"reason": reason, **resolution}


def hydrate_statblock_spellcasting(
    parsed: Any,
    candidates: Sequence[ContentArtifact],
    *,
    source_key: str,
    rule_refs: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Bind parsed spells to an exact active catalog or a source-bound action.

    The caller owns campaign access and supplies the already-authorized active
    artifact set. This function owns only reusable D&D normalization and
    deterministic card construction.
    """

    sheet = deepcopy(parsed.sheet)
    spellcasting = deepcopy(parsed.spellcasting)
    if not isinstance(spellcasting, dict):
        return sheet, []

    sheet["spellcasting"]["ability"] = spellcasting["ability"]
    sheet["spellcasting"]["attack_bonus_override"] = spellcasting.get("attack_bonus")
    sheet["spellcasting"]["save_dc_override"] = spellcasting.get("save_dc")
    sheet["spellcasting"]["spell_slots"] = {
        str(level): {
            "label": f"Level {level} spell slots",
            "value": int(count),
            "max": int(count),
            "recovers_on": "long_rest",
            "source_key": source_key,
            "slot_level": int(level),
        }
        for level, count in dict(spellcasting.get("slots") or {}).items()
    }

    prepared_ids: list[str] = []
    warnings: list[str] = []
    innate = bool(spellcasting.get("innate"))
    for specification in spellcasting.get("spells", []):
        name = str(specification.get("name") or "").strip()
        raw_level = specification.get("level")
        level = int(raw_level) if raw_level is not None else None
        exact = [
            item
            for item in candidates
            if str(dict(item[2].get("card") or {}).get("name") or "").casefold()
            == name.casefold()
            and (
                level is None
                or int(dict(item[2].get("card") or {}).get("level", 0) or 0) == level
            )
        ]
        if len(exact) == 1:
            pack_id, version, artifact = exact[0]
            card = deepcopy(dict(artifact.get("card") or {}))
            card.pop("classes", None)
            card.update(
                {
                    "id": str(artifact["id"]),
                    "pack_id": pack_id,
                    "pack_version": version,
                    "rule_refs": list(artifact.get("rule_refs") or []),
                    "mechanic_refs": list(artifact.get("mechanic_refs") or []),
                }
            )
            action_description = str(specification.get("action_description") or "").strip()
            if action_description and isinstance(card.get("resolution"), dict):
                card = overlay_spell_attack_card(card, action_description)
        elif len(exact) > 1:
            warnings.append(f"{name}: multiple active spell artifacts match the statblock entry")
            continue
        else:
            action_description = str(specification.get("action_description") or "").strip()
            if not action_description:
                warnings.append(
                    f"{name}: no active spell artifact or complete statblock action exists"
                )
                continue
            display_name = re.sub(
                r"\s*\([^)]*\)\s*$",
                "",
                str(specification.get("action_name") or name),
            ).strip()
            slug = ascii_slug(name)
            range_match = re.search(
                r"(?i)range\s+(\d+)(?:\s*/\s*(\d+))?\s*ft",
                action_description,
            )
            normal_range = int(range_match.group(1)) if range_match else 0
            long_range = int(range_match.group(2) or 0) if range_match else 0
            card = {
                "id": f"{source_key}.spell.{slug}",
                "source_key": source_key,
                "name": display_name,
                "level": int(level or 0),
                "definition": {
                    "casting_time": "1 action",
                    "range": {
                        "kind": "distance" if range_match else "special",
                        "normal_ft": normal_range,
                        "long_ft": long_range,
                    },
                    "duration": {"kind": "instantaneous"},
                    "components": {},
                    "effect": action_description,
                },
                "custom_definition": {
                    "source": source_key,
                    "component_details": "not_repeated_in_statblock",
                },
                "ruling_requirements": [
                    _ruling_requirement(
                        "Confirm the statblock spell's omitted component details "
                        "from available source and scene facts.",
                        "source_or_scene_fact",
                    ),
                    _ruling_requirement(
                        "Adjudicate the source-described spell effect.",
                        "generic_spell_effect",
                    ),
                ],
                "notes": (
                    "Source-bound statblock spell action. Component legality and "
                    "effect settlement return to Agent-as-DM adjudication."
                ),
                "pack_id": "",
                "pack_version": "",
                "rule_refs": list(rule_refs),
                "mechanic_refs": [],
            }
            resolution = spell_attack_action_resolution(action_description)
            if resolution is not None:
                card["resolution"] = resolution
                card["mechanic_refs"] = [SPELL_RESOLUTION_MECHANIC_ID]
                card["notes"] = (
                    "Source-bound statblock spell attack. Component legality still "
                    "returns to Agent-as-DM adjudication."
                )
                card["ruling_requirements"] = card["ruling_requirements"][:1]
                warnings.append(
                    f"{display_name}: source-bound statblock spell requires component ruling"
                )
            else:
                warnings.append(
                    f"{display_name}: source-bound statblock spell requires component "
                    "and effect ruling"
                )

        card["grant"] = {
            "source_type": "statblock",
            "source_key": source_key,
            "method": "innate" if innate else "known",
        }
        card["access"] = {
            "known": True,
            "prepared": True,
            "always_prepared": True,
            "in_spellbook": False,
            "ritual_available": False,
            "at_will": bool(specification.get("at_will", False)),
        }
        if innate:
            custom_definition = dict(card.get("custom_definition") or {})
            source_name = str(specification.get("source_name") or name).strip()
            source_qualifier = str(specification.get("source_qualifier") or "").strip()
            custom_definition.update(
                {
                    "statblock_source_name": source_name,
                    "innate_spellcasting": True,
                }
            )
            if source_qualifier:
                custom_definition["statblock_source_qualifier"] = source_qualifier
                requirements = list(card.get("ruling_requirements") or [])
                requirements.append(
                    _ruling_requirement(
                        f"Apply the statblock spell qualifier exactly: {source_qualifier}.",
                        "source_or_scene_fact",
                    )
                )
                card["ruling_requirements"] = requirements
            if spellcasting.get("no_material_components"):
                components = dict(dict(card.get("definition") or {}).get("components") or {})
                components.update(
                    {
                        "material": False,
                        "material_description": "",
                        "material_cost_cp": 0,
                        "consumed": False,
                    }
                )
                card.setdefault("definition", {})["components"] = components
                custom_definition["statblock_omits_material_components"] = True
            uses_per_day = specification.get("uses_per_day")
            if uses_per_day is not None:
                independent = bool(specification.get("uses_are_independent"))
                usage_group = str(specification.get("usage_group") or "daily").strip()
                resource_key = (
                    f"innate_spell:{card['id']}"
                    if independent
                    else f"innate_spell_group:{source_key}:{usage_group}"
                )
                custom_definition["innate_resource_key"] = resource_key
                existing_resource = dict(sheet.setdefault("resources", {}).get(resource_key) or {})
                resource = {
                    "label": (
                        f"{card['name']} ({int(uses_per_day)}/day)"
                        if independent
                        else f"Innate spell group ({int(uses_per_day)}/day)"
                    ),
                    "value": int(uses_per_day),
                    "max": int(uses_per_day),
                    "recovers_on": "long_rest",
                    "source_key": source_key,
                }
                if existing_resource and existing_resource != resource:
                    raise ValueError("innate spell usage group has conflicting resource limits")
                sheet["resources"][resource_key] = resource
            card["custom_definition"] = custom_definition
        sheet["content"]["spells"].append(card)
        prepared_ids.append(str(card["id"]))

    sheet["spellcasting"]["preparation"] = {
        "mode": "known",
        "max_prepared": len(prepared_ids),
        "changes_on": "manual",
        "selected_spell_ids": prepared_ids,
    }
    return validate_character_sheet(sheet), warnings
