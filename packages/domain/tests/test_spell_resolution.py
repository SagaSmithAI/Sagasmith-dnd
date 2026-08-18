import pytest

from sagasmith_dnd.spell_resolution import (
    audit_spell_resolution_paths,
    effective_spell_resolution,
    known_spell_resolution,
    normalize_spell_resolution,
    overlay_spell_attack_action,
    overlay_spell_attack_card,
    scaled_roll_expression,
    spell_attack_action_resolution,
    spell_attack_count,
    spell_resolution_path,
)


def test_reviewed_spell_resolutions_scale_without_free_form_formulas() -> None:
    scorching_ray = known_spell_resolution("Scorching Ray")
    assert scorching_ray is not None
    assert spell_attack_count(scorching_ray, cast_level=2) == 3
    assert spell_attack_count(scorching_ray, cast_level=5) == 6
    assert (
        scaled_roll_expression(scorching_ray["attack"]["damage"], cast_level=5, actor_level=5)
        == "2d6"
    )


def test_spell_resolution_audit_includes_known_cantrips_and_all_paths() -> None:
    sheet = {
        "content": {
            "spells": [
                {
                    "id": "light",
                    "name": "Light",
                    "level": 0,
                    "access": {"known": True},
                    "ruling_requirements": [
                        {
                            "default_resolver": "agent",
                            "ruling_kind": "generic_spell_effect",
                            "source_excerpt": "One object sheds bright light.",
                        }
                    ],
                },
                {
                    "id": "pulse",
                    "name": "Pulse",
                    "level": 1,
                    "access": {"prepared": True},
                    "resolution_plan": {"id": "pulse-plan"},
                },
                {
                    "id": "blank",
                    "name": "Blank",
                    "level": 1,
                    "access": {"known": True},
                },
            ]
        }
    }

    audit = audit_spell_resolution_paths(sheet)

    assert audit["complete"] is False
    assert audit["cantrip_spell_ids"] == ["light"]
    assert audit["available_spell_ids"] == ["light", "pulse", "blank"]
    assert audit["missing_spell_ids"] == ["blank"]
    assert audit["counts"]["agent_ruling"] == 1
    assert audit["counts"]["semantic_plan"] == 1
    assert spell_resolution_path(sheet["content"]["spells"][0]) == "agent_ruling"
    assert (
        spell_resolution_path(
            {
                "id": "dnd5e.content.srd2014.spell.fly",
                "name": "Fly",
                "level": 3,
                "access": {"prepared": True},
            }
        )
        == "engine_mechanic"
    )

    fireball = known_spell_resolution("Fireball")
    assert fireball is not None
    assert (
        scaled_roll_expression(fireball["save"]["damage"], cast_level=5, actor_level=9)
        == "8d6 + 2d6"
    )
    sacred_flame = known_spell_resolution("Sacred Flame")
    assert sacred_flame is not None
    assert (
        scaled_roll_expression(sacred_flame["save"]["damage"], cast_level=0, actor_level=11)
        == "3d8"
    )
    assert (
        scaled_roll_expression(
            {"base_dice": "1d4", "per_slot_dice": "1d4", "slot_base_level": 1},
            cast_level=3,
            actor_level=5,
            flat_modifier=3,
        )
        == "1d4 + 2d4 + 3"
    )


def test_spell_resolution_rejects_unreviewed_fields_and_invalid_dice() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        normalize_spell_resolution(
            {
                "kind": "healing",
                "targeting": {"mode": "creature"},
                "healing": {"base_dice": "1d4", "raw_formula": "999d999"},
            }
        )
    with pytest.raises(ValueError, match="NdM"):
        normalize_spell_resolution(
            {
                "kind": "spell_attack",
                "targeting": {"mode": "creature"},
                "attack": {
                    "mode": "ranged",
                    "damage": {"base_dice": "1d6 + 99", "damage_type": "fire"},
                },
            }
        )


def test_lightning_bolt_has_a_strict_scaling_line_contract() -> None:
    lightning_bolt = known_spell_resolution("Lightning Bolt")

    assert lightning_bolt is not None
    assert lightning_bolt["targeting"]["area"] == {
        "shape": "line",
        "length_ft": 100,
        "width_ft": 5,
    }
    assert lightning_bolt["save"] == {
        "ability": "dexterity",
        "success": "half",
        "damage": {
            "base_dice": "8d6",
            "per_slot_dice": "1d6",
            "slot_base_level": 3,
            "cantrip_dice": {},
            "damage_type": "lightning",
        },
        "save_dc_override": None,
        "ignores_cover": False,
        "on_failed_save_ruling": "",
    }
    assert (
        scaled_roll_expression(
            lightning_bolt["save"]["damage"],
            cast_level=5,
            actor_level=9,
        )
        == "8d6 + 2d6"
    )


def test_effective_resolution_hydrates_only_exact_builtin_spell_ids() -> None:
    hydrated = effective_spell_resolution(
        {
            "id": "dnd5e.content.srd2014.spell.lightning-bolt",
            "name": "Legacy Lightning Bolt",
            "resolution": None,
        }
    )

    assert hydrated == known_spell_resolution("Lightning Bolt")
    assert (
        effective_spell_resolution(
            {
                "id": "custom.spell.lightning-bolt",
                "name": "Lightning Bolt",
                "resolution": None,
            }
        )
        is None
    )


def test_spell_resolution_rejects_mixed_area_dimensions() -> None:
    with pytest.raises(ValueError, match="cannot define"):
        normalize_spell_resolution(
            {
                "kind": "saving_throw",
                "targeting": {
                    "mode": "area",
                    "area": {
                        "shape": "line",
                        "radius_ft": 20,
                        "length_ft": 100,
                        "width_ft": 5,
                    },
                },
                "save": {
                    "ability": "dexterity",
                    "success": "half",
                    "damage": {
                        "base_dice": "8d6",
                        "damage_type": "lightning",
                    },
                },
            }
        )


def test_statblock_spell_attack_overlay_keeps_reviewed_ray_count() -> None:
    description = (
        "*Ranged Spell Attack:* +6 to hit, range 60 ft., one target. *Hit:* 7 (2d6) fire damage."
    )
    parsed = spell_attack_action_resolution(description)
    assert parsed is not None
    assert parsed["attack"]["attack_bonus_override"] == 6
    assert parsed["attack"]["range_ft_override"] == 60

    core = known_spell_resolution("Scorching Ray")
    assert core is not None
    overlaid = overlay_spell_attack_action(core, description)
    assert overlaid["attack"]["count"]["base"] == 3
    assert overlaid["attack"]["attack_bonus_override"] == 6
    assert overlaid["attack"]["damage"]["base_dice"] == "2d6"


def test_statblock_spell_attack_card_keeps_display_and_settlement_consistent() -> None:
    description = (
        "*Ranged Spell Attack:* +6 to hit, range 60 ft., one target. *Hit:* 7 (2d6) fire damage."
    )
    core = {
        "id": "dnd5e.content.srd2014.spell.scorching-ray",
        "definition": {
            "casting_time": "1 action",
            "range": {"kind": "distance", "normal_ft": 120, "long_ft": 120},
            "components": {"verbal": True, "somatic": True},
            "effect": "Base spell text with a 120-foot range.",
        },
        "resolution": known_spell_resolution("Scorching Ray"),
        "notes": "",
    }

    overlaid = overlay_spell_attack_card(core, description)

    assert overlaid["definition"]["range"] == {
        "kind": "distance",
        "normal_ft": 60,
        "long_ft": 0,
    }
    assert overlaid["definition"]["components"] == {"verbal": True, "somatic": True}
    assert overlaid["definition"]["effect"] == description
    assert overlaid["resolution"]["attack"]["range_ft_override"] == 60
    assert overlaid["resolution"]["attack"]["count"]["base"] == 3
    assert "Statblock action overrides" in overlaid["notes"]
