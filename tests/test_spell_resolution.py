import pytest

from sagasmith_dnd.spell_resolution import (
    known_spell_resolution,
    normalize_spell_resolution,
    overlay_spell_attack_action,
    overlay_spell_attack_card,
    scaled_roll_expression,
    spell_attack_action_resolution,
    spell_attack_count,
)


def test_reviewed_spell_resolutions_scale_without_free_form_formulas() -> None:
    scorching_ray = known_spell_resolution("Scorching Ray")
    assert scorching_ray is not None
    assert spell_attack_count(scorching_ray, cast_level=2) == 3
    assert spell_attack_count(scorching_ray, cast_level=5) == 6
    assert (
        scaled_roll_expression(
            scorching_ray["attack"]["damage"], cast_level=5, actor_level=5
        )
        == "2d6"
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
        scaled_roll_expression(
            sacred_flame["save"]["damage"], cast_level=0, actor_level=11
        )
        == "3d8"
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


def test_statblock_spell_attack_overlay_keeps_reviewed_ray_count() -> None:
    description = (
        "*Ranged Spell Attack:* +6 to hit, range 60 ft., one target. "
        "*Hit:* 7 (2d6) fire damage."
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
        "*Ranged Spell Attack:* +6 to hit, range 60 ft., one target. "
        "*Hit:* 7 (2d6) fire damage."
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
        "long_ft": 60,
    }
    assert overlaid["definition"]["components"] == {"verbal": True, "somatic": True}
    assert overlaid["definition"]["effect"] == description
    assert overlaid["resolution"]["attack"]["range_ft_override"] == 60
    assert overlaid["resolution"]["attack"]["count"]["base"] == 3
    assert "Statblock action overrides" in overlaid["notes"]
