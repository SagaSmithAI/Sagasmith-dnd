from copy import deepcopy

from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.combat_engine import (
    apply_damage_to_sheet,
    force_move_directly_away,
    pay_witch_bolt_sustain_action,
    reconcile_witch_bolt_concentration,
    reconcile_witch_bolt_range,
    resolve_common_action,
    start_encounter,
    start_witch_bolt_tether,
)
from sagasmith_dnd.content_import import audit_release_semantic_validation
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.lifecycle import advance_effect_durations
from sagasmith_dnd.spell_resolution import scaled_roll_expression, spell_resolution_path
from sagasmith_dnd.spells import consume_spell_cast
from sagasmith_dnd.standard_content import build_standard2014_content
from sagasmith_dnd.standard_spell_ids import (
    CORE_BLADE_WARD_MECHANIC_ID,
    CORE_BLADE_WARD_SPELL_ID,
    CORE_DESTRUCTIVE_WAVE_SPELL_ID,
    CORE_WITCH_BOLT_MECHANIC_ID,
    CORE_WITCH_BOLT_SPELL_ID,
    STANDARD_2014_CONTENT_PACK_ID,
    STANDARD_2014_CONTENT_PACK_VERSION,
)


def _spell_card(spell_id: str) -> dict:
    _manifest, artifacts = build_standard2014_content()
    artifact = next(item for item in artifacts if item["id"] == spell_id)
    card = deepcopy(artifact["card"])
    card.pop("classes", None)
    card.update(
        id=artifact["id"],
        pack_id=STANDARD_2014_CONTENT_PACK_ID,
        pack_version=STANDARD_2014_CONTENT_PACK_VERSION,
        rule_refs=list(artifact["rule_refs"]),
        mechanic_refs=list(artifact["mechanic_refs"]),
    )
    card["access"]["known"] = True
    card["access"]["prepared"] = True
    return card


def _caster_with_spell(spell_id: str, *, slot_level: int | None = None) -> dict:
    sheet = default_character_sheet()
    sheet["content"]["spells"] = [_spell_card(spell_id)]
    if slot_level is not None:
        sheet["spellcasting"]["spell_slots"] = {
            str(slot_level): {
                "label": f"Level {slot_level} spell slots",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest",
                "source_key": "wizard",
                "slot_level": slot_level,
            }
        }
    return validate_character_sheet(sheet)


def _actor(identifier: str, *, initiative: int, position: dict[str, int]) -> dict:
    sheet = default_character_sheet()
    return {
        "id": identifier,
        "name": identifier,
        "initiative": initiative,
        "position": position,
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
    }


def test_standard_2014_mechanics_pack_is_separate_from_srd_and_native() -> None:
    manifest, artifacts = build_standard2014_content()

    assert manifest["id"] == STANDARD_2014_CONTENT_PACK_ID
    assert manifest["version"] == STANDARD_2014_CONTENT_PACK_VERSION == "1.4.0"
    assert {item["id"] for item in artifacts} == {
        CORE_BLADE_WARD_SPELL_ID,
        CORE_DESTRUCTIVE_WAVE_SPELL_ID,
        CORE_WITCH_BOLT_SPELL_ID,
        "dnd5e.content.standard2014.species.dragonborn",
        "dnd5e.content.standard2014.species.drow",
        "dnd5e.content.standard2014.species.forest-gnome",
        "dnd5e.content.standard2014.species.tiefling",
    }
    assert all(
        str(item["rule_refs"][0]).startswith("book:players-handbook-2014:") for item in artifacts
    )
    assert spell_resolution_path(artifacts[0]["card"]) == "engine_mechanic"
    witch_bolt = next(item for item in artifacts if item["id"] == CORE_WITCH_BOLT_SPELL_ID)
    assert spell_resolution_path(witch_bolt["card"]) == "structured_resolution"
    registered = {item.id for item in get_core_rule_pack("2014").boundaries}
    assert set(manifest["native_mechanic_refs"]) <= registered
    validation = audit_release_semantic_validation(artifacts)
    assert manifest["resolution_policy"] == "build_time_complete"
    assert manifest["semantic_validation"] == validation
    assert validation == {
        "schema_version": 1,
        "complete": True,
        "artifact_count": 7,
        "resolved_count": 7,
        "modes": {
            "agent_ruling": 4,
            "kernel_mechanic": 3,
            "static_grant": 7,
        },
        "unresolved": [],
        "first_use_compilation_required": False,
    }

    species = {
        item["card"]["name"]: item["card"]["grants"]
        for item in artifacts
        if item["kind"] == "species"
    }
    dragonborn = species["Dragonborn"]["damage_affinity_choice"]
    assert len(dragonborn["options"]) == 10
    assert dragonborn["activity"]["damage_by_level"] == {
        "1": "2d6",
        "6": "3d6",
        "11": "4d6",
        "16": "5d6",
    }
    assert [item["name"] for item in species["Drow"]["spell_grants"]] == [
        "Dancing Lights",
        "Faerie Fire",
        "Darkness",
    ]
    assert species["Forest Gnome"]["spell_grants"][0]["name"] == "Minor Illusion"
    hellish_rebuke = species["Tiefling"]["spell_grants"][1]
    assert hellish_rebuke["minimum_level"] == 3
    assert hellish_rebuke["casting_overrides"] == {"fixed_cast_level": 2}


def test_blade_ward_resists_only_weapon_attack_bps_until_next_turn_end() -> None:
    cast = consume_spell_cast(
        _caster_with_spell(CORE_BLADE_WARD_SPELL_ID),
        spell_id=CORE_BLADE_WARD_SPELL_ID,
    )

    assert cast["automatic_effect"] == "blade_ward"
    assert cast["concentration_started"] is False
    assert cast["ruling_required"] == ["verbal_component", "somatic_component"]
    active = next(item for item in cast["sheet"]["effects"] if item["id"] == cast["effect_id"])
    assert active["source"] == CORE_BLADE_WARD_MECHANIC_ID
    weapon = apply_damage_to_sheet(
        cast["sheet"],
        amount=9,
        damage_type="slashing",
        weapon_attack=True,
    )
    assert weapon["applied_amount"] == 4
    assert weapon["adjustment"] == "resistant"
    assert weapon["defense_sources"] == [f"spell:{cast['effect_id']}"]

    nonweapon = apply_damage_to_sheet(
        cast["sheet"],
        amount=9,
        damage_type="slashing",
        weapon_attack=False,
    )
    elemental_weapon = apply_damage_to_sheet(
        cast["sheet"],
        amount=9,
        damage_type="fire",
        weapon_attack=True,
    )
    assert nonweapon["applied_amount"] == 9
    assert elemental_weapon["applied_amount"] == 9

    casting_turn = advance_effect_durations(cast["sheet"], period="turn_end")
    assert casting_turn["expired"] == []
    next_turn = advance_effect_durations(casting_turn["sheet"], period="turn_end")
    assert next_turn["expired"] == [cast["effect_id"]]


def test_witch_bolt_uses_scaled_initial_damage_and_fixed_repeat_action() -> None:
    sheet = _caster_with_spell(CORE_WITCH_BOLT_SPELL_ID, slot_level=3)
    card = sheet["content"]["spells"][0]
    initial = scaled_roll_expression(
        card["resolution"]["attack"]["damage"],
        cast_level=3,
        actor_level=5,
    )
    assert initial == "1d12 + 2d12"
    assert card["mechanic_refs"] == [
        "dnd5e.core.spell.structured_resolution",
        CORE_WITCH_BOLT_MECHANIC_ID,
    ]

    cast = consume_spell_cast(
        sheet,
        spell_id=CORE_WITCH_BOLT_SPELL_ID,
        cast_level=3,
    )
    concentration = next(
        item
        for item in cast["sheet"]["effects"]
        if item.get("active") and item.get("concentration")
    )
    encounter = start_encounter(
        [
            _actor("caster", initiative=20, position={"x": 0, "y": 0}),
            _actor("target", initiative=10, position={"x": 4, "y": 0}),
        ],
        ruleset="2014",
    )
    tethered = start_witch_bolt_tether(
        encounter,
        caster_id="caster",
        target_id="target",
        spell_id=CORE_WITCH_BOLT_SPELL_ID,
        concentration_effect_id=concentration["id"],
    )
    tether = tethered["effect"]
    assert tether["repeat_damage"] == "1d12"
    assert tether["range_ft"] == 30

    sustained = pay_witch_bolt_sustain_action(
        tethered["encounter"],
        actor_id_value="caster",
        effect_id=tether["id"],
        target_total_cover=False,
    )
    assert sustained["status"] == "ready"
    assert sustained["payment"] == "main_action"
    assert sustained["effect"]["active"] is True

    another_encounter = start_encounter(
        [
            _actor("caster", initiative=20, position={"x": 0, "y": 0}),
            _actor("target", initiative=10, position={"x": 4, "y": 0}),
        ],
        ruleset="2014",
    )
    another_tether = start_witch_bolt_tether(
        another_encounter,
        caster_id="caster",
        target_id="target",
        spell_id=CORE_WITCH_BOLT_SPELL_ID,
        concentration_effect_id=concentration["id"],
    )
    dodged = resolve_common_action(
        another_tether["encounter"],
        actor_id_value="caster",
        action="dodge",
    )
    ended = next(
        item for item in dodged["ongoing_effects"] if item["id"] == another_tether["effect"]["id"]
    )
    assert ended["active"] is False
    assert ended["ended_reason"] == "caster_used_action_for_another_purpose"

    ranged_encounter = deepcopy(another_tether["encounter"])
    target = next(item for item in ranged_encounter["combatants"] if item["actor_id"] == "target")
    target["position"] = {"x": 7, "y": 0}
    reconciled = reconcile_witch_bolt_range(ranged_encounter)
    assert reconciled["ended"][0]["ended_reason"] == "target_outside_spell_range"

    concentration_ended = reconcile_witch_bolt_concentration(
        another_tether["encounter"],
        actor_id_value="caster",
        active_concentration_effect_ids=set(),
    )
    assert concentration_ended["ended"][0]["ended_reason"] == "concentration_ended"

    forced = force_move_directly_away(
        another_tether["encounter"],
        source_actor_id="caster",
        target_actor_id="target",
        distance_ft=15,
    )
    assert forced["ended_witch_bolt_tether_ids"] == [another_tether["effect"]["id"]]
