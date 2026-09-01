import re
from pathlib import Path

import pytest

from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.rule_engine import core_receipts, resolution_context

REQUIRED_BOUNDARIES = {
    "dnd5e.core.ability_generation",
    "dnd5e.core.armor_class.unarmored",
    "dnd5e.core.check.armor_stealth_disadvantage",
    "dnd5e.core.weapon.reach",
    "dnd5e.core.initiative.tie",
    "dnd5e.core.action.edition_list",
    "dnd5e.core.attack.cover",
    "dnd5e.core.attack.condition_source",
    "dnd5e.core.attack.help",
    "dnd5e.core.attack.hidden_reveal",
    "dnd5e.core.attack.ranged_close_combat",
    "dnd5e.core.damage.zero_hp",
    "dnd5e.core.damage.knockout",
    "dnd5e.core.damage.stable_recovery",
    "dnd5e.core.item.healing_potion",
    "dnd5e.core.movement.prone_crawl_stand",
    "dnd5e.core.movement.grapple_source",
    "dnd5e.core.movement.occupied_destination",
    "dnd5e.core.movement.difficult_terrain",
    "dnd5e.core.reaction.opportunity_path",
    "dnd5e.core.reaction.post_hit_defense",
    "dnd5e.core.spell.shield_attack_ac",
    "dnd5e.core.spell.structured_resolution",
    "dnd5e.core.ready.action",
    "dnd5e.core.save.restrained_dexterity",
    "dnd5e.core.rest.hit_dice",
    "dnd5e.core.rest.exhaustion",
    "dnd5e.core.spell.cantrip_ritual_level",
    "dnd5e.core.spell.material_components",
    "dnd5e.core.spell.preparation",
    "dnd5e.core.progression.experience",
    "dnd5e.core.mcp.combat_mutation_guard",
    "dnd5e.core.mcp.opportunity_melee_only",
    "dnd5e.core.mcp.reaction_defense_atomicity",
    "dnd5e.core.mcp.shield_attack_reaction_atomicity",
    "dnd5e.core.mcp.save_damage_atomicity",
    "dnd5e.core.mcp.duration_clock",
    "dnd5e.core.mcp.combat_spell_boundary",
    "dnd5e.core.mcp.pending_ruling_atomicity",
}

EDITION_BOUNDARIES = {
    "2014": {
        "dnd5e.core.action.dodge",
        "dnd5e.core.activity.orc_aggressive",
        "dnd5e.core.activity.recharge",
        "dnd5e.core.check.jack_of_all_trades",
        "dnd5e.core.damage.relentless_endurance",
        "dnd5e.core.movement.dwarf_heavy_armor_speed",
        "dnd5e.core.rest.natural_recovery",
        "dnd5e.core.rest.song_of_rest",
        "dnd5e.core.rest.sorcerous_restoration",
        "dnd5e.core.spell.pact_magic",
        "dnd5e.core.spell.fly",
        "dnd5e.core.progression.hp_hit_dice",
        "dnd5e.core.progression.spellcasting",
        "dnd5e.core.save.evasion",
    },
    "2024": {
        "dnd5e.core.activity.action_surge",
        "dnd5e.core.activity.divine_spark",
        "dnd5e.core.activity.recharge",
        "dnd5e.core.activity.second_wind",
        "dnd5e.core.progression.hp_hit_dice",
        "dnd5e.core.progression.spellcasting",
        "dnd5e.core.rest.arcane_recovery",
        "dnd5e.core.rest.sorcerous_restoration",
    },
}


def test_builtin_core_pack_wraps_every_preserved_boundary() -> None:
    for edition in ("2014", "2024"):
        pack = get_core_rule_pack(edition)
        ids = {item.id for item in pack.boundaries}
        assert REQUIRED_BOUNDARIES | EDITION_BOUNDARIES[edition] <= ids
        assert len(ids) == len(pack.boundaries)
        assert all(
            item.implementation and item.test_refs and item.citation for item in pack.boundaries
        )
        assert all(item.editions == (edition,) for item in pack.boundaries)
        assert all(
            item.evidence
            and item.evidence[0].edition == edition
            and item.evidence[0].citation == item.citation
            and item.evidence[0].test_refs == item.test_refs
            for item in pack.boundaries
        )


def test_2024_core_pack_never_borrows_a_2014_rulebook_citation() -> None:
    pack = get_core_rule_pack("2024")

    assert all("2014" not in item.citation for item in pack.boundaries)
    assert all(
        item.citation.startswith(("bundled:srd2024/", "runtime:")) for item in pack.boundaries
    )
    assert {
        "dnd5e.core.activity.recharge",
        "dnd5e.core.rest.arcane_recovery",
        "dnd5e.core.rest.sorcerous_restoration",
    } <= {item.id for item in pack.boundaries}
    assert "dnd5e.core.action.dodge" not in {item.id for item in pack.boundaries}


def test_dnd_runtime_never_emits_an_unregistered_core_boundary() -> None:
    source_root = Path(__file__).parents[1] / "src" / "sagasmith_dnd"
    emitted = {
        mechanic_id
        for path in source_root.rglob("*.py")
        if path.name != "core_rule_pack.py"
        for mechanic_id in re.findall(
            r'["\'](dnd5e\.core\.[a-z0-9_.]+)["\']',
            path.read_text(encoding="utf-8"),
        )
    }
    registered = {
        item.id for edition in ("2014", "2024") for item in get_core_rule_pack(edition).boundaries
    }

    assert emitted <= registered


def test_effective_fingerprint_includes_core_pack() -> None:
    first = resolution_context(
        {"edition": "2014", "fingerprint": "extensions", "lock": [], "mechanics": []}
    )
    second = resolution_context(
        {"edition": "2024", "fingerprint": "extensions", "lock": [], "mechanics": []}
    )
    assert first.core_pack.id == "dnd5e.core.2014"
    assert second.core_pack.id == "dnd5e.core.2024"
    assert first.fingerprint != second.fingerprint

    receipt = core_receipts(first, ["dnd5e.core.activity.resource_accounting"], "activity.consume")[
        0
    ]
    assert receipt["core_pack_fingerprint"] == first.core_pack.fingerprint
    assert receipt["ruleset_fingerprint"] == first.fingerprint


def test_unknown_core_edition_never_falls_back() -> None:
    with pytest.raises(ValueError, match="unsupported D&D core edition"):
        get_core_rule_pack("2030")
