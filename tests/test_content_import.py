import pytest

from sagasmith_dnd.content_import import (
    compiled_artifacts_from_candidates,
    extract_content_candidates,
    module_statblock_review_candidates,
    normalize_2014_statblock_candidate,
    validate_selection_ready_artifacts,
)
from sagasmith_dnd.statblocks import StatblockImportError, parse_2014_statblock


def test_extracts_review_required_catalog_candidates() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "chunk-fireball",
                "heading_path": ["Chapter 3", "Fireball"],
                "content": "3rd-level evocation spell\nCasting Time: 1 action",
                "page_start": 42,
                "page_end": 42,
            },
            {
                "id": "chunk-background",
                "heading_path": ["Backgrounds", "City Watch"],
                "content": "Skill Proficiencies: Athletics, Insight",
            },
        ]
    )
    assert [item["kind"] for item in candidates] == ["spell", "background"]
    assert all(item["review_status"] == "pending" for item in candidates)
    assert all(item["application_state"] == "catalog_only" for item in candidates)


def test_extractor_aggregates_all_chunks_from_one_structural_entry() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "spell-a",
                "heading_path": ["Spells", "Fireball"],
                "content": "3rd-level evocation\nCasting Time: 1 action\nRange: 150 feet",
                "page_start": 10,
                "page_end": 10,
            },
            {
                "id": "spell-b",
                "heading_path": ["Spells", "Fireball"],
                "content": "Components: V, S, M\nDuration: Instantaneous\nA bright streak flashes.",
                "page_start": 10,
                "page_end": 11,
            },
        ]
    )

    assert len(candidates) == 1
    assert candidates[0]["source_chunk_ids"] == ["spell-a", "spell-b"]
    assert candidates[0]["page_start"] == 10
    assert candidates[0]["page_end"] == 11
    assert "bright streak" in candidates[0]["artifact"]["card"]["description"]


def test_extractor_requires_structural_signals_instead_of_loose_keywords() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "ordinary",
                "heading_path": ["Advice", "Schools and Weapons"],
                "content": (
                    "This chapter discusses a school, a weapon, armor, and a legendary feat "
                    "as ordinary examples without defining player content."
                ),
            },
            {
                "id": "monster",
                "heading_path": ["Monsters", "Goblin"],
                "content": (
                    "Armor Class 15\nHit Points 7\nSpeed 30 ft.\n"
                    "STR 8 DEX 14 CON 10 INT 10 WIS 8 CHA 8\nChallenge 1/4"
                ),
            },
        ]
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("statblock", "Goblin")
    ]


def test_module_statblock_chunks_become_review_ready_without_guessing_ocr() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "GOBLIN"]
    chunks = [
        {
            "id": "goblin-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Small humanoid (goblinoid), neutral evil Armor Class 15 "
                "(leather armor, shield) Hit Points 7 (2d6) Speed 30 ft."
            ),
            "page_start": 58,
            "page_end": 58,
        },
    ]
    values = {
        "STR": "8 (-1)",
        "DEX": "14 (+2)",
        "CON": "10 (+0)",
        "INT": "10 (+0)",
        "WIS": "8 (-1)",
        "CHA": (
            "8 (-1) Skills Stealth +6 Senses darkvision 60 ft., passive Perception 9 "
            "Languages Common, Goblin Challenge 1/4 (50 XP) Nimble Escape. "
            "The goblin can take the Disengage or Hide action as a bonus action."
        ),
    }
    chunks.extend(
        {
            "id": f"goblin-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 58,
            "page_end": 58,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "goblin-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Scimitar. Melee Weapon Attack: +4 to hit, reach 5 ft., one target. "
                "Hit: 5 (ld6 + 2) slashing damage. Shortbow. Ranged Weapon Attack: "
                "+4 to hit, range 80 ft./320 ft., one target. Hit: 5 (1d6 + 2) "
                "piercing damage. Heavy Crossbow. Ranged Weapon Attack: +2 to hit, "
                "range 100/400 ft., one target. Hit: 5 (ldl0) piercing damage."
            ),
            "page_start": 58,
            "page_end": 58,
        }
    )

    candidates = module_statblock_review_candidates(chunks, source_title="Lost Mine")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["name"] == "GOBLIN"
    assert candidate["execution_state"] == "review_ready"
    assert candidate["source_scene_ids"] == ["monster-scene"]
    assert candidate["page_start"] == 58
    assert candidate["page_end"] == 58
    assert candidate["validation"]["challenge_rating"] == "1/4"
    assert "**Armor Class** 15 (leather armor, shield)" in candidate["normalized_content"]
    assert "***Scimitar***. Melee Weapon Attack" in candidate["normalized_content"]
    assert "Hit: 5 (1d6 + 2) slashing damage" in candidate["normalized_content"]
    assert "Hit: 5 (1d10) piercing damage" in candidate["normalized_content"]


def test_module_statblock_keeps_effect_only_hit_clause_inside_its_attack() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "GIANT SPIDER"]
    chunks = [
        {
            "id": "spider-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Large beast, unaligned Armor Class 14 (natural armor) "
                "Hit Points 26 (4d10 + 4) Speed 30 ft., climb 30 ft."
            ),
            "page_start": 58,
            "page_end": 58,
        }
    ]
    values = {
        "STR": "14 (+2)",
        "DEX": "16 (+3)",
        "CON": "12 (+1)",
        "INT": "2 (-4)",
        "WIS": "11 (+0)",
        "CHA": (
            "4 (-3) Skills Stealth +7 Senses blindsight 10 ft., darkvision 60 ft., "
            "passive Perception 10 Languages — Challenge 1 (200 XP)"
        ),
    }
    chunks.extend(
        {
            "id": f"spider-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 58,
            "page_end": 58,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "spider-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Bite. Melee Weapon Attack: +5 to hit, reach 5 ft., one creature. "
                "Hit: 7 (1d8 + 3) piercing damage. Web (Recharge 5-6). "
                "Ranged Weapon Attack: +5 to hit, range 30 ft./60 ft., one creature. "
                "Hit: The target is restrained by webbing. As an action, the restrained "
                "target can make a DC 12 Strength check, bursting the webbing on a "
                "success. The webbing can also be attacked and destroyed (AC 10; hp 5; "
                "vulnerable to fire damage; immune to bludgeoning, poison, and psychic "
                "damage). Usually found underground, the lair of "
                "a giant spider is often festooned with webs holding helpless victims."
            ),
            "page_start": 58,
            "page_end": 58,
        }
    )

    candidate = module_statblock_review_candidates(chunks)[0]

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "Hit: The target is restrained by webbing." in candidate["normalized_content"]
    assert "***The target is restrained by webbing***" not in candidate["normalized_content"]
    assert "Usually found underground" not in candidate["normalized_content"]
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-review:giant-spider",
    )
    web = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["id"] == "web-recharge-5-6"
    )
    assert web["mechanics"]["on_hit_effect"].endswith(
        "immune to bludgeoning, poison, and psychic damage)."
    )
    assert candidate["validation"]["warnings"] == [
        "Web (Recharge 5-6): on-hit effect requires DM settlement"
    ]


def test_text_layout_recovery_scopes_split_guard_without_images() -> None:
    base = ["Appendix B: Nonplayer Characters", "CULT FANATIC"]
    chunks = [
        {
            "id": "gladiator-reaction",
            "ordinal": 3839,
            "heading_path": [*base, "REACTIONS"],
            "content": "Parry. The gladiator adds 3 to its AC.",
        },
        {
            "id": "guard-core",
            "ordinal": 3840,
            "heading_path": [*base, "GUARD"],
            "content": (
                "Medium humanoid (any race), any alignment Armor Class 16 "
                "(chain shirt, shield) Hit Points 11 (2d8 + 2) Speed 30ft."
            ),
        },
        {
            "id": "guard-str",
            "ordinal": 3841,
            "heading_path": [*base, "STR"],
            "content": "13 (+1)",
        },
        {
            "id": "guard-dex",
            "ordinal": 3842,
            "heading_path": [*base, "DEX"],
            "content": "12 (+1) Skills Perception +2",
        },
        {
            "id": "guard-con",
            "ordinal": 3843,
            "heading_path": [*base, "CON"],
            "content": "12 (+1) Senses passive Perception 12",
        },
        {
            "id": "guard-int",
            "ordinal": 3844,
            "heading_path": [*base, "INT"],
            "content": "10 (+0)",
        },
        {
            "id": "guard-wis",
            "ordinal": 3845,
            "heading_path": [*base, "WIS"],
            "content": (
                "11 (+0) Languages any one language (usually Common) "
                "Challenge 1/8 (25 XP)"
            ),
        },
        {
            "id": "guard-actions",
            "ordinal": 3846,
            "heading_path": [*base, "ACTIONS"],
            "content": "",
        },
        {
            "id": "guard-cha",
            "ordinal": 3847,
            "heading_path": [*base, "CHA"],
            "content": (
                "10 (+0) Spear. Melee or Ranged Weapon Attack: +3 to hit, "
                "reach 5 ft. or range 20f60 ft., one target. Hit: 4 "
                "(1d6 + 1) piercing damage. Guards include members of a city "
                "watch, sentries in a citadel or fortified town."
            ),
        },
        {
            "id": "knight-core",
            "ordinal": 3848,
            "heading_path": [*base, "KNIGHT"],
            "content": (
                "Medium humanoid (any race), any alignment Armor Class 18 "
                "(plate) Hit Points 52 (8d8 + 16) Speed 30ft."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("Guard", chunks)
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="rule-source:monster-manual",
        rule_refs=candidate["source_chunk_ids"],
        name="Mill Ruse Guard",
    )

    assert candidate["source_chunk_ids"] == [
        "guard-core",
        "guard-str",
        "guard-dex",
        "guard-con",
        "guard-int",
        "guard-wis",
        "guard-actions",
        "guard-cha",
    ]
    assert parsed.name == "Mill Ruse Guard"
    assert parsed.challenge_rating == "1/8"
    assert parsed.experience_points == 25
    assert "range 20/60 ft." in candidate["normalized_content"]
    assert "Guards include members" not in candidate["normalized_content"]
    assert "knight" not in candidate["normalized_content"].casefold()
    spear = next(item for item in parsed.sheet["inventory"]["items"] if item["name"] == "Spear")
    assert spear["mechanics"]["attack_bonus_override"] == 3
    assert spear["mechanics"]["thrown_normal_range_ft"] == 20
    assert spear["mechanics"]["thrown_long_range_ft"] == 60
    assert parsed.warnings == ()
    with pytest.raises(StatblockImportError, match="CULT FANATIC"):
        normalize_2014_statblock_candidate("CULT FANATIC", chunks)


def test_text_layout_recovery_ignores_ocr_noise_inside_creature_heading() -> None:
    base = ["Appendix B: Nonplayer Characters", "CUSTOMIZING NPCS"]
    chunks = [
        {
            "id": "berserker-core",
            "ordinal": 1,
            "heading_path": [*base, "BER,SERKER"],
            "content": (
                "Medium humanoid (any race), any chaotic alignment Armor Class 13 "
                "(hide armor) Hit Points 67 (9d8 + 27) Speed 30 ft."
            ),
        },
        *[
            {
                "id": f"berserker-{ability.casefold()}",
                "ordinal": index + 2,
                "heading_path": [*base, ability],
                "content": value,
            }
            for index, (ability, value) in enumerate(
                {
                    "STR": "16 (+3)",
                    "DEX": "12 (+1)",
                    "CON": "17 (+3)",
                    "INT": "9 (-1)",
                    "WIS": (
                        "11 (+0) Languages any one language (usually Common) "
                        "Challenge 2 (450 XP)"
                    ),
                    "CHA": (
                        "9 (-1) Reckless. At the start of its turn, the berserker "
                        "can gain advantage on all melee weapon attack rolls during "
                        "that turn."
                    ),
                }.items()
            )
        ],
        {
            "id": "berserker-actions",
            "ordinal": 8,
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Greataxe. Melee Weapon Attack: +5 to hit, reach 5 ft., one target. "
                "Hit: 9 (1d12 + 3) slashing damage. Ha iling from uncivilized "
                "lands, unpredictable berserkers seek conflict wherever they can."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("Berserker", chunks)
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="rule-source:monster-manual",
    )

    assert candidate["name"] == "Berserker"
    assert candidate["source_chunk_ids"][0] == "berserker-core"
    assert candidate["normalized_content"].startswith("# Berserker\n")
    assert "Ha iling from" not in candidate["normalized_content"]
    greataxe = next(
        item for item in parsed.sheet["inventory"]["items"] if item["name"] == "Greataxe"
    )
    assert greataxe["mechanics"]["on_hit_effect"] == ""
    assert not any(warning.startswith("Greataxe:") for warning in parsed.warnings)


def test_text_layout_recovery_repairs_generic_action_bracket_and_range_ocr() -> None:
    base = ["Appendix A: Miscellaneous Creatures", "GIANT SPIDER"]
    chunks = [
        {
            "id": "spider-core",
            "ordinal": 1,
            "heading_path": base,
            "content": (
                "Large beast, unaligned Armor Class 14 (natural armor) "
                "Hit Points 26 (4d10 + 4) Speed 30 ft., climb 30 ft."
            ),
        },
        *[
            {
                "id": f"spider-{ability.casefold()}",
                "ordinal": index + 2,
                "heading_path": [*base, ability],
                "content": value,
            }
            for index, (ability, value) in enumerate(
                {
                    "STR": "14 (+2)",
                    "DEX": "16 (+3) Skills Stealth +7",
                    "CON": "12 (+1)",
                    "INT": "2 (-4)",
                    "WIS": "11 (+0)",
                    "CHA": (
                        "4 (-3) Senses blindsight 10 ft., darkvision 60 ft., "
                        "passive Perception 10 Languages Challenge 1 (200 XP)"
                    ),
                }.items()
            )
        ],
        {
            "id": "spider-actions",
            "ordinal": 8,
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Bite. Melee Weapon Attack: +5 to hit, reach 5 ft., one creature. "
                "Hit: 7 (1d8 + 3) piercing damage. "
                "Web (Recharge 5-6}. Ranged Weapon Attack: +5 to hit, "
                "range 30f60 ft., one creature. Hit: The target is restrained "
                "by webbing."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("Giant Spider", chunks)
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="rule-source:monster-manual",
    )

    attacks = {
        item["name"]: item["mechanics"]
        for item in parsed.sheet["inventory"]["items"]
        if item["kind"] == "weapon"
    }
    assert set(attacks) == {"Bite", "Web (Recharge 5-6)"}
    assert attacks["Web (Recharge 5-6)"]["normal_range_ft"] == 30
    assert attacks["Web (Recharge 5-6)"]["long_range_ft"] == 60
    assert any(
        warning.startswith("Web (Recharge 5-6):")
        for warning in parsed.warnings
    )


def test_module_statblock_recovers_flattened_actions_and_ranged_distance() -> None:
    base = ["Appendix D", "LANGDEDROSA CYANWRATH"]
    chunks = [
        {
            "id": "cyanwrath-core",
            "scene_id": "statblock-scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (half-dragon), lawful evil Armor Class 17 (splint) "
                "Hit Points 57 (6d12 + 18) Speed 30 ft."
            ),
        }
    ]
    values = {
        "STR": "19 (+4)",
        "DEX": "13 (+1)",
        "CON": "16 (+3)",
        "INT": "10 (+0)",
        "WIS": "14 (+2)",
        "CHA": (
            "12 (+1) Saving Throws Str +6, Con +5 Skills Athletics +6, "
            "Intimidation +3, Perception +4 Damage Resistances lightning "
            "Senses blindsight 10 ft., darkvision 60 ft., passive Perception 14 "
            "Languages Common, Draconic Challenge 4 (1,100 XP) "
            "Action Surge (Recharges when Langdedrosa Finishes a Short or Long Rest). "
            "On his turn, Langdedrosa can take one additional action. "
            "Improved Critical. Langdedrosa's weapon attacks score a critical hit "
            "on a roll of 19 or 20. A ctions ____________________________________ "
            "Multiattack. Langdedrosa attacks twice, either with his greatsword or spear. "
            "G reatsword. Melee Weapon Attack: +6 to hit, reach 5 ft., one target. "
            "Hit: 11 (2d6 + 4) slashing damage. "
            "Spear. Melee or Ranged Weapon Attack: +6 to hit, reach 5 ft. or "
            "ranged 20 ft./60 ft., one target. Hit: 7 (1d6 + 4) piercing damage. "
            "Lightning Breath (Recharge 5-6). Langdedrosa breathes lightning in a "
            "30-foot line that is 5 feet wide. Each creature in the line must make "
            "a DC 13 Dexterity saving throw, taking 22 (4d10) lightning damage on "
            "a failed save, or half as much damage on a successful one."
        ),
    }
    chunks.extend(
        {
            "id": f"cyanwrath-{ability.casefold()}",
            "scene_id": "statblock-scene",
            "heading_path": [*base, ability],
            "content": content,
        }
        for ability, content in values.items()
    )

    candidate = module_statblock_review_candidates(chunks)[0]
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-candidate:cyanwrath",
    )

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "## Actions" in candidate["normalized_content"]
    assert "***Greatsword***." in candidate["normalized_content"]
    assert "range 20/60 ft." in candidate["normalized_content"]
    weapons = {
        item["name"]: item
        for item in parsed.sheet["inventory"]["items"]
        if item["kind"] == "weapon"
    }
    assert set(weapons) == {"Greatsword", "Spear"}
    assert weapons["Spear"]["mechanics"]["thrown_normal_range_ft"] == 20
    assert weapons["Spear"]["mechanics"]["thrown_long_range_ft"] == 60
    multiattack = next(
        item for item in parsed.sheet["content"]["activities"] if item["name"] == "Multiattack"
    )
    options = {
        option["id"]: option["attacks"]
        for option in multiattack["choices"]["multiattack_options"]
    }
    assert options == {
        "melee": [
            {"weapon_id": "greatsword", "attack_mode": "melee", "count": 2}
        ],
        "melee-2": [{"weapon_id": "spear", "attack_mode": "melee", "count": 2}],
        "ranged": [{"weapon_id": "spear", "attack_mode": "ranged", "count": 2}],
    }
    assert all("Multiattack composition" not in warning for warning in parsed.warnings)
    action_names = {item["name"] for item in parsed.sheet["content"]["activities"]}
    feature_names = {item["name"] for item in parsed.sheet["content"]["features"]}
    assert "Lightning Breath (Recharge 5-6)" in action_names
    assert "Improved Critical" in feature_names


def test_module_statblock_marks_named_actor_spellcasting_trait() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "NEZZNAR"]
    chunks = [
        {
            "id": "nezznar-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (elf), neutral evil Armor Class 11 "
            "Hit Points 27 (6dS) Speed 30 ft."
            ),
            "page_start": 59,
            "page_end": 59,
        }
    ]
    values = {
        "STR": "9 (-1)",
        "DEX": "13 (+1)",
        "CON": "10 (+0)",
        "INT": "16 (+3)",
        "WIS": "14 (+2)",
        "CHA": (
            "13 (+1) Saving Throws Int +5, Wis +4 Skills Arcana +5, Perception +4 "
            "Senses darkvision 120 ft., passive Perception 14 Languages Elvish, "
            "Undercommon Challenge 2 (450 XP) Special Equipment. Nezznar has a "
            "spider staff. Fey Ancestry. Nezznar has advantage on saving throws "
            "against being charmed. Innate Spellcasting. Nezznar can innately cast "
            "the following spells, requiring no material components:\n"
            "- At will: dancing lights\n- 1/day each: darkness, faerie fire (save DC 12)\n"
            "Spellcasting. Nezznar is a 4th-level spellcaster "
            "that uses Intelligence as his spellcasting ability (spell save DC 13; "
            "+5 to hit with spell attacks). Nezznar has the following spells prepared "
            "from the wizard's spell list: Cantrips (at will): mage hand, ray offrost, "
            "shocking grasp 1st Level (4 slots): mage armor, magic missile, shield "
            "- 2nd Level (3 slots): invisihility, suggestion"
        ),
    }
    chunks.extend(
        {
            "id": f"nezznar-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 59,
            "page_end": 59,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "nezznar-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Spider Staff. Melee Weapon Attack: +1 to hit, reach 5 ft., one target. "
                "Hit: 2 (1d6 - 1) bludgeoning damage plus 3 (1d6) poison damage. "
                "Drow are a subterranean race that worships Lolth, the Demon Queen of "
                "Spiders. Drow society is strictly matriarchal."
            ),
            "page_start": 59,
            "page_end": 59,
        }
    )

    candidate = module_statblock_review_candidates(chunks)[0]
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-candidate:nezznar",
    )

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "***Spellcasting***. Nezznar is a 4th-level spellcaster" in candidate[
        "normalized_content"
    ]
    assert "***Demon Queen of Spiders***" not in candidate["normalized_content"]
    assert "Drow are a subterranean race" not in candidate["normalized_content"]
    assert "**Hit Points** 27 (6d8)" in candidate["normalized_content"]
    assert parsed.spellcasting is not None
    assert parsed.spellcasting["slots"] == {"1": 4, "2": 3}
    assert [spell["name"] for spell in parsed.spellcasting["spells"]] == [
        "mage hand",
        "ray of frost",
        "shocking grasp",
        "mage armor",
        "magic missile",
        "shield",
        "invisibility",
        "suggestion",
    ]
    spider_staff = parsed.sheet["inventory"]["items"][0]
    assert spider_staff["name"] == "Spider Staff"
    assert spider_staff["mechanics"]["additional_damage"] == [
        {
            "damage_formula": "1d6",
            "damage_bonus": 0,
            "damage_type": "poison",
        }
    ]
    assert spider_staff["mechanics"]["on_hit_effect"] == ""
    assert not any(warning.startswith("Spider Staff:") for warning in parsed.warnings)


def test_module_statblock_repairs_bounded_spellcasting_ocr() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "EVILMAGE"]
    chunks = [
        {
            "id": "evil-mage-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (human), lawful evil Armor Class 12 "
                "Hit Points 22 (5d8) Speed 30 ft."
            ),
            "page_start": 57,
            "page_end": 57,
        },
    ]
    values = {
        "STR": "9 (-1)",
        "DEX": "14 (+2)",
        "CON": "11 (+0)",
        "INT": "17 (+3)",
        "WIS": "12 (+1)",
        "CHA": (
            "11 (+0) Saving Throws Int +5, Wis +3 Skills Arcana +5, History +5 "
            "Senses passive Perception 11 Languages Common, Draconic, Dwarvish, Elvish "
            "Challenge 1 (200 XP) Spellcasting. The mage is a 4th·level spellcaster "
            "that uses Intelligence as its spellcasting ability (spell save DC 13; "
            "+5 to hit with spell attacks). The mage knows the following spells from "
            "the wizard's spell list: Cantrips (at will): light, mage hand, shocking "
            "grasp l st Level (4 slots): charm person, magic missile 2nd Level "
            "(3 slots): hold person, misty step"
        ),
    }
    chunks.extend(
        {
            "id": f"evil-mage-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 57,
            "page_end": 57,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "evil-mage-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Quarterstaff. Melee Weapon Attack: +1 to hit, reach 5 ft., one target. "
                "Hit: 3 (1d8 - 1) bludgeoning damage."
            ),
            "page_start": 57,
            "page_end": 57,
        }
    )

    candidates = module_statblock_review_candidates(chunks, source_title="Lost Mine")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["execution_state"] == "review_ready"
    assert candidate["validation"]["warnings"] == []
    assert "4th-level spellcaster" in candidate["normalized_content"]
    assert "1st level (4 slots)" in candidate["normalized_content"]

    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-candidate:evil-mage",
    )
    assert parsed.spellcasting is not None
    assert parsed.spellcasting["ability"] == "intelligence"
    assert parsed.spellcasting["save_dc"] == 13
    assert parsed.spellcasting["attack_bonus"] == 5
    assert parsed.spellcasting["class_lists"] == ["wizard"]
    assert parsed.spellcasting["slots"] == {"1": 4, "2": 3}
    assert [item["name"] for item in parsed.spellcasting["spells"]] == [
        "light",
        "mage hand",
        "shocking grasp",
        "charm person",
        "magic missile",
        "hold person",
        "misty step",
    ]
    assert parsed.warnings == ()


def test_module_statblock_candidate_keeps_ambiguous_ocr_blocked() -> None:
    base = ["Monsters", "HOBGOBLIN"]
    chunks = [
        {
            "id": "core",
            "scene_id": "scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (goblinoid), lawful evil Armor Class lS "
                "(chain mail, shield) Hit Points 11 (2d8 + 2) Speed 30 ft."
            ),
        }
    ]
    for ability, score in zip(
        ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
        (13, 12, 12, 10, 10, 9),
        strict=True,
    ):
        suffix = (
            " Challenge 1/2 (100 XP)"
            if ability == "CHA"
            else ""
        )
        chunks.append(
            {
                "id": ability,
                "scene_id": "scene",
                "heading_path": [*base, ability],
                "content": f"{score} (+0){suffix}",
            }
        )
    chunks.append(
        {
            "id": "actions",
            "scene_id": "scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Longsword. Melee Weapon Attack: +3 to hit, reach 5 ft., one target. "
                "Hit: 5 (1d8 + 1) slashing damage."
            ),
        }
    )

    candidate = module_statblock_review_candidates(chunks)[0]

    assert candidate["execution_state"] == "blocked"
    assert candidate["review_status"] == "manual_review_required"
    assert "Armor Class or Hit Points is invalid" in candidate["review_error"]


def test_module_statblock_candidates_keep_scene_local_ordinals_together() -> None:
    dragon_path = ["Appendix B: Monsters", "dragonclaw"]
    other_path = ["Appendix B: Monsters", "Other Guard"]
    chunks = [
        {
            "id": "a-dragon-core",
            "scene_id": "dragon-scene",
            "ordinal": 0,
            "heading_path": dragon_path,
            "content": (
                "Medium humanoid (human), neutral evil Armor Class 14 "
                "(leather armor) Hit Points 16 (3d8 + 3) Speed 30 ft."
            ),
            "page_start": 90,
            "page_end": 90,
        },
        {
            "id": "z-other-core",
            "scene_id": "other-scene",
            "ordinal": 0,
            "heading_path": other_path,
            "content": (
                "Medium humanoid (human), neutral Armor Class 12 "
                "Hit Points 11 (2d8 + 2) Speed 30 ft."
            ),
            "page_start": 12,
            "page_end": 12,
        },
    ]
    other_values = {
        "STR": "10 (+0)",
        "DEX": "10 (+0)",
        "CON": "12 (+1)",
        "INT": "10 (+0)",
        "WIS": "10 (+0)",
        "CHA": "10 (+0) Challenge 1/4 (50 XP)",
    }
    chunks.append(
        {
            "id": "dragon-abilities",
            "scene_id": "dragon-scene",
            "ordinal": 1,
            "heading_path": [*dragon_path, "STR DEX CO N INT WIS CHA"],
            "content": (
                "9 (-1) 16 (+3) 13 (+1) 11 (+0) 10 (+0) 12 (+1) "
                "Saving Throws Wis +2 Skills Deception +3, Stealth +5 "
                "Senses passive Perception 10 Languages Common, Draconic "
                "Challenge 1 (200 XP) Pack Tactics. The dragonclaw has advantage "
                "on an attack roll when an ally is within 5 feet of the target."
            ),
            "page_start": 90,
            "page_end": 90,
        }
    )
    for ordinal, ability in enumerate(
        ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
        start=1,
    ):
        chunks.append(
            {
                "id": f"other-{ability.casefold()}",
                "scene_id": "other-scene",
                "ordinal": ordinal,
                "heading_path": [*other_path, ability],
                "content": other_values[ability],
                "page_start": 12,
                "page_end": 12,
            }
        )
    chunks.extend(
        (
            {
                "id": "dragon-actions",
                "scene_id": "dragon-scene",
                "ordinal": 7,
                "heading_path": [*dragon_path, "A c t i o n s"],
                "content": (
                    "Multiattack. The dragonclaw attacks twice with its scimitar. "
                    "Scimitar. M elee Weapon Attack: +5 to hit, reach 5 ft., one "
                    "target. Hit: 6 (1d6 + 3) slashing damage."
                ),
                "page_start": 90,
                "page_end": 90,
            },
            {
                "id": "other-actions",
                "scene_id": "other-scene",
                "ordinal": 7,
                "heading_path": [*other_path, "ACTIONS"],
                "content": (
                    "Club. Melee Weapon Attack: +2 to hit, reach 5 ft., one target. "
                    "Hit: 2 (1d4) bludgeoning damage."
                ),
                "page_start": 12,
                "page_end": 12,
            },
        )
    )

    candidates = module_statblock_review_candidates(chunks)
    dragon = next(item for item in candidates if item["name"] == "dragonclaw")

    assert dragon["execution_state"] == "review_ready", dragon.get("review_error")
    assert dragon["source_scene_ids"] == ["dragon-scene"]
    assert dragon["source_chunk_ids"] == [
        "a-dragon-core",
        "dragon-abilities",
        "dragon-actions",
    ]
    assert dragon["validation"]["challenge_rating"] == "1"
    assert "Other Guard" not in dragon["normalized_content"]


def test_module_statblock_recovers_attack_heading_with_spaced_ocr_connector() -> None:
    path = ["Appendix A: Monsters", "dragonfang"]
    chunks = [
        {
            "id": "core",
            "scene_id": "scene",
            "ordinal": 0,
            "heading_path": path,
            "content": (
                "Medium humanoid (human), neutral evil Armor Class 15 "
                "(studded leather) Hit Points 78 (12d8 + 24) Speed 30 ft."
            ),
        },
        {
            "id": "details",
            "scene_id": "scene",
            "ordinal": 1,
            "heading_path": [*path, "STR DEX CON INT WIS CHA"],
            "content": (
                "11 (+0) 16 (+3) 14 (+2) 12 (+1) 12 (+1) 14 (+2) "
                "Senses passive Perception 11 Languages Common, Draconic "
                "Challenge 5 (1,800 XP)"
            ),
        },
        {
            "id": "actions",
            "scene_id": "scene",
            "ordinal": 2,
            "heading_path": [*path, "A ct io ns"],
            "content": (
                "Multiattack. The dragonfang attacks twice with its shortsword. "
                "Shortsword. Melee Weapon Attack: +5 to hit, reach 5 ft., one "
                "target. Hit: 6 (1d6 + 3) piercing damage. "
                "Orb o f Dragon?s Breath (2/Day). Ranged Spell Attack: +5 to hit, "
                "range 90 ft., one target. Hit: 22 (5d8) force damage."
            ),
        },
    ]

    candidate = module_statblock_review_candidates(chunks)[0]

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "***Orb o f Dragon?s Breath (2/Day)***." in candidate["normalized_content"]


def test_class_features_are_not_misclassified_as_feats() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "class",
                "heading_path": ["Chapter 3: Classes", "Barbarian"],
                "content": "The Barbarian",
            },
            {
                "id": "class-features",
                "heading_path": ["Chapter 3: Classes", "Barbarian", "Class Features"],
                "content": "Class Features\nHit Dice: 1d12 per barbarian level",
            },
            {
                "id": "rage",
                "heading_path": [
                    "Chapter 3: Classes",
                    "Barbarian",
                    "Class Features",
                    "Rage",
                ],
                "content": "At 1st level, you fight with primal ferocity.",
            },
            {
                "id": "spell",
                "heading_path": ["Chapter 11: Spells", "Spark"],
                "content": "1st-level evocation\nCasting Time: 1 action",
            },
        ],
        source_title="D&D 5E - Player's Handbook",
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("class", "Barbarian"),
        ("feature", "Rage"),
        ("spell", "Spark"),
    ]
    assert candidates[0]["source_chunk_ids"] == ["class", "class-features", "rage"]


def test_source_title_recovers_a_supplement_class_heading() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "class-features",
                "heading_path": ["Class Features"],
                "content": "Class Features\nHit Dice: 1d8 per artificer level",
            },
            {
                "id": "proficiencies",
                "heading_path": ["Class Features", "Proficiencies"],
                "content": "Saving Throw Proficiencies: Constitution, Intelligence",
            },
            {
                "id": "infuse-item",
                "heading_path": ["Class Features", "Infuse Item"],
                "content": "At 2nd level, you gain the ability to imbue mundane items.",
            },
        ],
        source_title="D&D 5E - UA - ArtificerV2",
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("class", "Artificer"),
        ("feature", "Infuse Item"),
    ]
    assert candidates[0]["source_chunk_ids"] == [
        "class-features",
        "proficiencies",
        "infuse-item",
    ]


def test_source_title_recovers_flat_ocr_class_headings() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "class-features",
                "heading_path": ["CLASS FEATURES"],
                "content": "As a blood hunter, you gain the following class features.",
            },
            {
                "id": "hit-points",
                "heading_path": ["HIT POINTS"],
                "content": "Hit Dice: 1d10 per blood hunter level",
            },
        ],
        source_title="D&D 5E - UA - Blood Hunter Class 1.2",
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("class", "Blood Hunter")
    ]
    assert candidates[0]["source_chunk_ids"] == ["class-features", "hit-points"]


def test_parent_catalog_does_not_duplicate_a_descendant_spell() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "catalog",
                "heading_path": ["Optional Spells"],
                "content": "Optional Spells",
            },
            {
                "id": "spark",
                "heading_path": ["Optional Spells", "Spark"],
                "content": "1st-level evocation\nCasting Time: 1 action",
            },
        ]
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("spell", "Spark")
    ]


def test_compiler_requires_review_and_selection_ready_structure() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "chunk-fireball",
                "heading_path": ["Spells", "Fireball"],
                "content": "3rd-level evocation spell\nCasting Time: 1 action",
            }
        ]
    )
    candidates[0]["review_status"] = "accepted"
    artifacts = compiled_artifacts_from_candidates(candidates, pack_id="dnd5e.xgte")
    assert artifacts[0]["application_state"] == "catalog_only"
    artifacts[0]["application_state"] = "selection_ready"
    assert "spell needs a nonempty classes list" in "\n".join(
        validate_selection_ready_artifacts(artifacts)
    )
    artifacts[0]["card"] = {
        "name": "Fireball",
        "level": 3,
        "classes": ["wizard"],
        "definition": {},
        "resolution": {
            "kind": "saving_throw",
            "targeting": {
                "mode": "area",
                "max_targets": 100,
                "area": {"shape": "sphere", "radius_ft": 20},
            },
            "save": {
                "ability": "dexterity",
                "success": "half",
                "damage": {
                    "base_dice": "8d6",
                    "damage_type": "fire",
                },
            },
        },
    }
    assert validate_selection_ready_artifacts(artifacts) == []


def test_selection_ready_spell_uses_character_definition_validation() -> None:
    artifact = {
        "id": "spell:chromatic-orb",
        "kind": "spell",
        "application_state": "selection_ready",
        "card": {
            "name": "Chromatic Orb",
            "level": 1,
            "classes": ["sorcerer", "wizard"],
            "resolution": {
                "kind": "spell_attack",
                "targeting": {"mode": "creature", "max_targets": 1},
                "attack": {
                    "mode": "ranged",
                    "count": {"base": 1},
                    "damage": {
                        "base_dice": "3d8",
                        "damage_type": "acid",
                    },
                },
            },
            "definition": {
                "duration": {
                    "kind": "instantaneous",
                    "unit": "",
                }
            },
        },
    }

    errors = validate_selection_ready_artifacts([artifact])

    assert errors == ["artifacts[0].card.definition.duration.unit is invalid"]
    del artifact["card"]["definition"]["duration"]["unit"]
    assert validate_selection_ready_artifacts([artifact]) == []


def test_compiler_rejects_duplicate_generated_ids() -> None:
    candidates = [
        {
            "id": "one",
            "kind": "feat",
            "name": "Lucky",
            "source_chunk_ids": ["one"],
            "review_status": "accepted",
            "artifact": {"kind": "feat", "card": {"name": "Lucky"}},
        },
        {
            "id": "two",
            "kind": "feat",
            "name": "Lucky",
            "source_chunk_ids": ["two"],
            "review_status": "accepted",
            "artifact": {"kind": "feat", "card": {"name": "Lucky"}},
        },
    ]
    with pytest.raises(ValueError, match="duplicate generated artifact id"):
        compiled_artifacts_from_candidates(candidates, pack_id="dnd5e.xgte")


def test_reviewed_extension_spell_resolution_binds_to_core_executor() -> None:
    candidate = {
        "id": "candidate:healing-spell",
        "kind": "spell",
        "name": "Restoring Word",
        "source_chunk_ids": ["chunk:restoring-word"],
        "review_status": "accepted",
        "application_state": "selection_ready",
        "artifact": {
            "kind": "spell",
            "application_state": "selection_ready",
            "card": {
                "name": "Restoring Word",
                "level": 1,
                "classes": ["cleric"],
                "definition": {},
                "resolution": {
                    "kind": "healing",
                    "targeting": {"mode": "creature", "requires_sight": True},
                    "healing": {
                        "base_dice": "1d4",
                        "per_slot_dice": "1d4",
                        "slot_base_level": 1,
                        "add_spellcasting_modifier": True,
                    },
                },
            },
        },
    }

    artifacts = compiled_artifacts_from_candidates([candidate], pack_id="dnd5e.extension")

    assert validate_selection_ready_artifacts(artifacts) == []
    assert artifacts[0]["mechanic_refs"] == ["dnd5e.core.spell.structured_resolution"]
    assert artifacts[0]["card"]["mechanic_refs"] == [
        "dnd5e.core.spell.structured_resolution"
    ]


def test_custom_mechanical_artifact_persists_a_source_bound_plan_template() -> None:
    artifact_id = "dnd5e.extension.feature.prismatic-pulse"
    excerpt = (
        "Prismatic Pulse. Each chosen creature must make a Wisdom saving throw, "
        "taking 3d8 radiant damage on a failed save."
    )
    candidate = {
        "id": "candidate:prismatic-pulse",
        "kind": "feature",
        "name": "Prismatic Pulse",
        "source_chunk_ids": ["chunk:prismatic-pulse"],
        "review_status": "accepted",
        "mechanical_scope": "mechanical",
        "application_state": "selection_ready",
        "artifact": {
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "card": {"name": "Prismatic Pulse"},
            "resolution_plan": {
                "schema_version": 1,
                "id": "dnd5e.extension.plan.prismatic-pulse",
                "source_card_id": artifact_id,
                "source_card_kind": "feature",
                "trigger": "action",
                "slots": {
                    "targets": {
                        "kind": "actor_ids",
                        "owner": "agent",
                        "description": "Creatures selected inside the reviewed source area.",
                        "minimum_items": 1,
                    }
                },
                "steps": [
                    {
                        "id": "save",
                        "op": "check.save",
                        "args": {
                            "target_ids": {"$slot": "targets"},
                            "ability": "wisdom",
                            "dc": 14,
                            "source": "Prismatic Pulse",
                            "success_reduction": "none",
                        },
                    },
                    {
                        "id": "damage",
                        "op": "damage.apply",
                        "args": {
                            "target_ids": {"$slot": "targets"},
                            "expression": "3d8",
                            "damage_type": "radiant",
                            "source": "Prismatic Pulse",
                            "reduction": {
                                "$result": "save.damage_reduction_by_actor_id"
                            },
                        },
                    },
                ],
                "citations": [
                    {
                        "source": "rule-source:custom",
                        "source_ref": {"chunk_id": "chunk:prismatic-pulse"},
                        "source_excerpt": excerpt,
                    }
                ],
            },
        },
    }

    artifacts = compiled_artifacts_from_candidates(
        [candidate],
        pack_id="dnd5e.extension",
    )

    assert validate_selection_ready_artifacts(artifacts) == []
    assert artifacts[0]["execution_state"] == "plan_ready"
    assert artifacts[0]["resolution_plan"]["source_card_id"] == artifact_id
    assert artifacts[0]["resolution_plan"]["fingerprint"]
    assert artifacts[0]["mechanic_refs"] == [
        "dnd5e.extension.plan.prismatic-pulse"
    ]


def test_custom_mechanical_artifact_cannot_escape_its_reviewed_source_chunks() -> None:
    candidate = {
        "id": "candidate:unsafe",
        "kind": "feature",
        "name": "Unsafe",
        "source_chunk_ids": ["chunk:reviewed"],
        "review_status": "accepted",
        "mechanical_scope": "mechanical",
        "application_state": "selection_ready",
        "artifact": {
            "id": "dnd5e.extension.feature.unsafe",
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "card": {"name": "Unsafe"},
            "resolution_plan": {
                "schema_version": 1,
                "id": "dnd5e.extension.plan.unsafe",
                "source_card_id": "dnd5e.extension.feature.unsafe",
                "source_card_kind": "feature",
                "trigger": "action",
                "slots": {},
                "steps": [
                    {
                        "id": "damage",
                        "op": "damage.apply",
                        "args": {
                            "target_ids": ["target"],
                            "amount": 1,
                            "damage_type": "force",
                            "source": "Unsafe",
                        },
                    }
                ],
                "citations": [
                    {
                        "source": "rule-source:other",
                        "source_ref": {"chunk_id": "chunk:not-reviewed"},
                        "source_excerpt": (
                            "This unrelated text must not authorize the imported effect."
                        ),
                    }
                ],
            },
        },
    }

    with pytest.raises(ValueError, match="reviewed source chunks"):
        compiled_artifacts_from_candidates(
            [candidate],
            pack_id="dnd5e.extension",
        )
