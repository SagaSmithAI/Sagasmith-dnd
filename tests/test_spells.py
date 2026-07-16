import pytest

from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.lifecycle import advance_effect_durations
from sagasmith_dnd.spells import (
    CORE_SHIELD_MECHANIC_ID,
    CORE_SHIELD_SPELL_ID,
    available_shield_attack_defenses,
    consume_readied_spell,
    consume_shield_reaction,
    consume_spell_cast,
    replace_prepared_spells,
)


def _spell(spell_id: str, *, level: int, concentration: bool = False) -> dict:
    return {
        "id": spell_id,
        "name": spell_id,
        "level": level,
        "access": {"known": True, "prepared": True, "ritual_available": False},
        "definition": {
            "casting_time": "1 action",
            "duration": {
                "kind": "timed",
                "value": 1,
                "unit": "minute",
                "concentration": concentration,
            },
        },
    }


def test_spell_slot_and_concentration_are_settled_from_card_data() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest", "source_key": ""}
    }
    sheet["content"]["spells"] = [_spell("bless", level=1, concentration=True)]
    result = consume_spell_cast(validate_character_sheet(sheet), spell_id="bless")
    assert result["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    assert result["concentration_started"] is True


def test_shield_reaction_pays_slot_and_expires_at_turn_start() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["ac"]["override"] = 13
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "1st",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "wizard",
        }
    }
    shield = _spell(CORE_SHIELD_SPELL_ID, level=1)
    shield["name"] = "Shield"
    shield["definition"]["casting_time"] = "1 reaction, which you take when hit"
    shield["definition"]["duration"] = {
        "kind": "timed",
        "value": 1,
        "unit": "round",
        "concentration": False,
    }
    shield["mechanic_refs"] = [CORE_SHIELD_MECHANIC_ID]
    sheet["content"]["spells"] = [shield]
    sheet = validate_character_sheet(sheet)

    assert available_shield_attack_defenses(sheet) == [
        {
            "id": CORE_SHIELD_SPELL_ID,
            "name": "Shield",
            "kind": "spell_armor_class_bonus",
            "bonus": 5,
            "spell_id": CORE_SHIELD_SPELL_ID,
            "cast_levels": [1],
            "cast_options": [
                {
                    "cast_level": 1,
                    "payment": {
                        "economy": "slots",
                        "level": 1,
                        "ritual": False,
                    },
                }
            ],
            "mechanic_id": CORE_SHIELD_MECHANIC_ID,
            "source_key": "",
            "rule_refs": [],
        }
    ]
    applied = consume_shield_reaction(
        sheet,
        spell_id=CORE_SHIELD_SPELL_ID,
        cast_level=1,
    )
    assert applied["payment"]["economy"] == "slots"
    assert applied["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    assert available_shield_attack_defenses(applied["sheet"]) == []
    assert derive_character_sheet(applied["sheet"])["armor_class"] == 18

    ended = advance_effect_durations(applied["sheet"], period="turn_end")
    assert derive_character_sheet(ended["sheet"])["armor_class"] == 18
    started = advance_effect_durations(ended["sheet"], period="turn_start")
    assert started["expired"] == [applied["effect_id"]]
    assert derive_character_sheet(started["sheet"])["armor_class"] == 13


def test_shield_name_without_source_bound_mechanic_is_not_executable() -> None:
    sheet = default_character_sheet()
    spell = _spell("homebrew-shield", level=1)
    spell["name"] = "Shield"
    spell["definition"]["casting_time"] = "1 reaction"
    sheet["content"]["spells"] = [spell]
    assert available_shield_attack_defenses(validate_character_sheet(sheet)) == []


def test_ritual_and_cantrip_do_not_spend_a_slot() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["ritual_casting"] = True
    spell = _spell("alarm", level=1)
    spell["access"]["ritual_available"] = True
    cantrip = _spell("light", level=0)
    sheet["content"]["spells"] = [spell, cantrip]
    sheet = validate_character_sheet(sheet)
    assert consume_spell_cast(sheet, spell_id="alarm", ritual=True)["payment"]["economy"] == "none"
    assert consume_spell_cast(sheet, spell_id="light")["payment"]["economy"] == "none"


def test_cantrip_and_ritual_reject_slot_levels() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["ritual_casting"] = True
    ritual = _spell("alarm", level=1)
    ritual["access"]["ritual_available"] = True
    sheet["content"]["spells"] = [ritual, _spell("light", level=0)]
    sheet = validate_character_sheet(sheet)
    with pytest.raises(ValueError, match="cantrips"):
        consume_spell_cast(sheet, spell_id="light", cast_level=1)
    with pytest.raises(ValueError, match="ritual casting"):
        consume_spell_cast(sheet, spell_id="alarm", ritual=True, cast_level=2)


def test_pact_magic_uses_its_recorded_slot_level() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["pact_magic"] = {
        "label": "Pact Magic",
        "value": 1,
        "max": 1,
        "slot_level": 3,
        "recovers_on": "short_rest",
        "source_key": "warlock",
    }
    sheet["content"]["spells"] = [_spell("fireball", level=3)]
    result = consume_spell_cast(validate_character_sheet(sheet), spell_id="fireball", cast_level=3)
    assert result["payment"]["economy"] == "pact_magic"
    assert result["cast_level"] == 3
    assert result["sheet"]["spellcasting"]["pact_magic"]["value"] == 0


def test_costly_material_component_requires_dm_confirmation() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest", "source_key": ""}
    }
    chromatic_orb = _spell("chromatic-orb", level=1)
    chromatic_orb["definition"]["components"] = {
        "material": True,
        "material_cost_cp": 5000,
        "consumed": False,
    }
    sheet["content"]["spells"] = [chromatic_orb]
    sheet = validate_character_sheet(sheet)
    with pytest.raises(ValueError, match="material_confirmed"):
        consume_spell_cast(sheet, spell_id="chromatic-orb")
    result = consume_spell_cast(
        sheet, spell_id="chromatic-orb", component_ruling={"material_confirmed": True}
    )
    assert "material_component" in result["ruling_required"]


def test_readied_spell_pays_now_and_replaces_existing_concentration() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest", "source_key": ""}
    }
    sheet["content"]["spells"] = [_spell("magic-missile", level=1)]
    sheet["effects"] = [
        {
            "id": "old-concentration",
            "name": "Old",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "magic-missile",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
            "changes": [],
            "description": "",
        }
    ]
    result = consume_readied_spell(validate_character_sheet(sheet), spell_id="magic-missile")
    assert result["payment"]["economy"] == "slots"
    assert result["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    active = [effect for effect in result["sheet"]["effects"] if effect["active"]]
    assert len(active) == 1
    assert active[0]["id"] == result["holding_effect_id"]
    assert active[0]["kind"] == "readied_spell"


def test_only_one_action_spells_can_be_readied() -> None:
    sheet = default_character_sheet()
    spell = _spell("healing-word", level=0)
    spell["definition"]["casting_time"] = "bonus action"
    sheet["content"]["spells"] = [spell]
    with pytest.raises(ValueError, match="one action"):
        consume_readied_spell(validate_character_sheet(sheet), spell_id="healing-word")


def test_readied_spell_requires_recorded_action_casting_time() -> None:
    sheet = default_character_sheet()
    spell = _spell("incomplete-spell", level=0)
    spell["definition"].pop("casting_time")
    sheet["content"]["spells"] = [spell]
    with pytest.raises(ValueError, match="one action"):
        consume_readied_spell(validate_character_sheet(sheet), spell_id="incomplete-spell")


def test_prepared_caster_cannot_cast_unprepared_known_spell() -> None:
    sheet = default_character_sheet()
    sheet["progression"] = {
        "level": 1,
        "classes": [{"name": "Cleric", "level": 1, "hit_die": 8}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 4,
        "changes_on": "long_rest",
        "selected_spell_ids": ["bless"],
    }
    bless = _spell("bless", level=1)
    command = _spell("command", level=1)
    sheet["content"]["spells"] = [bless, command]
    sheet = validate_character_sheet(sheet)
    with pytest.raises(ValueError, match="not available"):
        consume_spell_cast(sheet, spell_id="command")


def test_2024_ranger_long_rest_replaces_only_one_spell() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["progression"] = {
        "level": 5,
        "classes": [{"name": "Ranger", "level": 5, "hit_die": 10}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 6,
        "changes_on": "long_rest",
        "selected_spell_ids": ["a", "b"],
    }
    spells = []
    for spell_id in ("a", "b", "c", "d"):
        spell = _spell(spell_id, level=1)
        spell["grant"] = {"source_type": "class", "source_key": "ranger"}
        spells.append(spell)
    sheet["content"]["spells"] = spells
    sheet = validate_character_sheet(sheet)

    changed = replace_prepared_spells(sheet, spell_ids=["a", "c"], event="long_rest")
    assert changed["added"] == ["c"]
    assert changed["removed"] == ["b"]
    with pytest.raises(ValueError, match="only 1 spell"):
        replace_prepared_spells(sheet, spell_ids=["c", "d"], event="long_rest")
    with pytest.raises(ValueError, match="replaces spells"):
        replace_prepared_spells(sheet, spell_ids=["a", "b", "c"], event="long_rest")


def test_2014_ranger_uses_spells_known_instead_of_preparation() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"] = {
        "level": 5,
        "classes": [{"name": "Ranger", "level": 5, "hit_die": 10}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 4,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    spell = _spell("cure-wounds", level=1)
    spell["grant"] = {"source_type": "class", "source_key": "ranger"}
    sheet["content"]["spells"] = [spell]
    with pytest.raises(ValueError, match="spells known"):
        replace_prepared_spells(
            validate_character_sheet(sheet),
            spell_ids=["cure-wounds"],
            event="setup",
        )


def test_wizard_preparation_uses_authoritative_spellbook_membership() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["progression"] = {
        "level": 3,
        "classes": [{"name": "Wizard", "level": 3, "hit_die": 6}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "spellbook",
        "max_prepared": 6,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spellbook"] = {
        "enabled": True,
        "spell_ids": ["magic-missile"],
    }
    magic_missile = _spell("magic-missile", level=1)
    shield = _spell("shield", level=1)
    for spell in (magic_missile, shield):
        spell["grant"] = {"source_type": "class", "source_key": "wizard"}
        spell["access"]["in_spellbook"] = True
    sheet["content"]["spells"] = [magic_missile, shield]
    sheet = validate_character_sheet(sheet)
    assert sheet["content"]["spells"][0]["access"]["in_spellbook"] is True
    assert sheet["content"]["spells"][1]["access"]["in_spellbook"] is False
    with pytest.raises(ValueError, match="spellbook"):
        replace_prepared_spells(sheet, spell_ids=["shield"], event="setup")
