from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd import character_schema
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.engine import roll as engine_roll
from test_official_expansions_mcp import _call, _config
from test_structured_spell_mcp import _slot, _spell

from sagasmith_dnd_mcp import server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
@pytest.mark.parametrize("edition", ["2014", "2024"])
@pytest.mark.parametrize("class_name", ["Monk", "Rogue"])
def test_real_core_evasion_is_applied_to_level_seven_classes(
    tmp_path: Path, edition: str, class_name: str, monkeypatch
) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            assert Path(character_schema.__file__).resolve().is_relative_to(workspace)
            assert Path(server_module.__file__).resolve().is_relative_to(workspace)
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": f"Evasion {edition} {class_name}",
                    "edition": edition,
                    "idempotency_key": "campaign",
                },
            )
            class_catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "class",
                        "query": class_name,
                    },
                },
            )
            class_artifact = next(item for item in class_catalog if item["name"] == class_name)
            sheet = default_character_sheet()
            sheet["progression"]["level"] = 1 if edition == "2014" else 7
            sheet["progression"]["classes"] = (
                []
                if edition == "2014"
                else [{"name": class_name, "level": 7, "subclass": "", "hit_die": 8}]
            )
            character = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": class_name, "sheet": sheet},
                    "idempotency_key": "character",
                },
            )
            if edition == "2014":
                class_applied = await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": character["id"],
                        "artifact_id": class_artifact["id"],
                        "selection": {
                            "skills": class_artifact["selection_requirements"]["skill_options"][
                                : class_artifact["selection_requirements"]["skill_choice_count"]
                            ]
                        },
                        "expected_revision": character["revision"],
                        "idempotency_key": "class",
                    },
                )
                assert "id" in class_applied or "character" in class_applied, class_applied
                character = class_applied.get("character", class_applied)
                for level in range(2, 8):
                    advanced = await _call(
                        server,
                        "character_state_change",
                        {
                            "character_id": character["id"],
                            "action": "level_advance",
                            "payload": {
                                "class_name": class_name,
                                "hp_method": "fixed",
                                "reason": "milestone",
                                "source_ref": (
                                    "bundled:srd2014/03_Characterization/Beyond_1st_Level.md"
                                ),
                            },
                            "expected_revision": character["revision"],
                            "idempotency_key": f"level-{level}",
                        },
                    )
                    assert "character" in advanced, advanced
                    character = advanced["character"]
                    assert "id" in character, advanced
            catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "feature",
                        "query": "Evasion",
                    },
                },
            )
            evasion = next(
                item
                for item in catalog
                if item["name"] == "Evasion"
                and str(item["id"]).casefold().endswith(f"{class_name.casefold()}-evasion")
            )
            applied = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": evasion["id"],
                    "selection": {},
                    "expected_revision": character["revision"],
                    "idempotency_key": "evasion",
                },
            )
            feature = next(
                item
                for item in applied["sheet"]["content"]["features"]
                if item["name"] == "Evasion"
            )
            trait = feature["choices"]["source_trait"]
            assert trait["kind"] == "evasion"
            assert trait["trigger"] == "dexterity_save_for_half_damage"
            assert trait["save_ability"] == "dexterity"
            assert trait["ordinary_successful_save"] == "half"
            assert trait["successful_save"] == "none"
            assert trait["failed_save"] == "half"
            assert len(feature["rule_refs"]) > 0
            if edition == "2014":
                assert "incapacitated" not in trait["unavailable_conditions"]
            else:
                assert "incapacitated" in trait["unavailable_conditions"]
            assert feature["pack_id"] == f"dnd5e.content.srd{edition}"
            assert feature["pack_version"]
            assert feature["rule_refs"]
            assert feature["mechanic_refs"]
            assert (
                await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": character["id"],
                        "artifact_id": evasion["id"],
                        "selection": {},
                        "expected_revision": character["revision"],
                        "idempotency_key": "evasion",
                    },
                )
                == applied
            )
            close_server(server)
            server = create_server(config)
            reloaded = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": character["id"]}},
            )
            assert reloaded["sheet"] == applied["sheet"]
            replayed = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": evasion["id"],
                    "selection": {},
                    "expected_revision": reloaded["revision"],
                    "idempotency_key": "evasion",
                },
            )
            assert replayed == applied

            # Preserve the actually applied feature and exercise it through
            # spell settlement, not a hand-written copy of the Evasion trait.
            injured_sheet = deepcopy(reloaded["sheet"])
            injured_sheet["conditions"] = ["incapacitated"]
            injured_sheet["combat"]["hp"] = {"value": 50, "max": 50, "temp": 0}
            target = await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": reloaded["id"],
                    "sheet": injured_sheet,
                    "expected_revision": reloaded["revision"],
                    "idempotency_key": "incapacitated",
                },
            )
            fireball = _spell("Fireball", 3, casting_time="1 action", range_ft=150)
            casters = []
            for index in range(2):
                caster_sheet = default_character_sheet()
                caster_sheet["abilities"]["intelligence"]["score"] = 18
                caster_sheet["spellcasting"].update(ability="intelligence", spell_slots=_slot(3))
                caster_sheet["content"]["spells"] = [fireball]
                casters.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": f"Caster {index}",
                                "sheet": caster_sheet,
                            },
                            "idempotency_key": f"caster-{index}",
                        },
                    )
                )
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                },
            )
            _, started = await server.call_tool(
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 16, "height_cells": 12},
                    "participant_ids": [casters[0]["id"], casters[1]["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": casters[0]["id"],
                            "initiative": 30,
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "actor_id": casters[1]["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 2},
                        },
                        {"actor_id": target["id"], "initiative": 10, "position": {"x": 8, "y": 0}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-combat",
                },
            )

            class DamageDice:
                count = 0

                def randint(self, lower, upper):
                    assert (lower, upper) == (1, 6)
                    self.count += 1
                    return 2 if self.count == 1 else 1

            class SaveDice:
                def randint(self, lower, upper):
                    assert (lower, upper) == (1, 20)
                    return 20 if save_succeeds else 1

            original_check = server_module.resolve_actor_check
            save_succeeds = True

            def saved_check(actor, **kwargs):
                assert actor["id"] == target["id"]
                kwargs["rng"] = SaveDice()
                return original_check(actor, **kwargs)

            monkeypatch.setattr(server_module, "resolve_actor_check", saved_check)
            monkeypatch.setattr(
                server_module, "roll", lambda expression: engine_roll(expression, rng=DamageDice())
            )
            revision = started["campaign_revision"]
            hp = 50
            cast_replays = []
            for index, caster in enumerate(casters):
                save_succeeds = index == 0
                cast_request = {
                    "campaign_id": campaign["id"],
                    "actor_id": caster["id"],
                    "spell_id": fireball["id"],
                    "cast_level": 3,
                    "declaration": {
                        "origin": {"x": 8, "y": 0},
                        "target_contexts": [
                            {"target_id": target["id"], "cover": "none"},
                        ],
                    },
                    "expected_revision": revision,
                    "idempotency_key": f"fireball-{index}",
                }
                _, settled = await server.call_tool("combat_cast_spell", cast_request)
                assert settled["status"] == "committed", settled
                assert settled["result"]["damage_roll"]["expression"] == "8d6"
                assert settled["result"]["damage_roll"]["total"] == 9
                target_result = settled["result"]["targets"][0]
                assert target_result["target_id"] == target["id"]
                assert target_result["save"]["success"] is save_succeeds
                amount = (
                    (0 if save_succeeds else 4)
                    if edition == "2014"
                    else (4 if save_succeeds else 9)
                )
                hp -= amount
                target_after = await _call(
                    server,
                    "character_query",
                    {
                        "view": "get",
                        "payload": {"character_id": target["id"]},
                    },
                )
                assert target_after["sheet"]["combat"]["hp"]["value"] == hp
                receipts = target_result["rule_receipts"]
                assert [receipt["mechanic_id"] for receipt in receipts] == (
                    ["dnd5e.core.save.evasion"] if edition == "2014" else []
                )
                for receipt in receipts:
                    assert receipt["core_pack_fingerprint"]
                    assert receipt["ruleset_fingerprint"]
                    assert receipt["citations"] == [
                        {
                            "source": "bundled:srd2014/02_Classes/Rogue.md#evasion",
                            "edition": "2014",
                        }
                    ]
                _, replay = await server.call_tool("combat_cast_spell", cast_request)
                assert replay == settled
                cast_replays.append((cast_request, settled))
                revision = settled["campaign_revision"]
                if index == 0:
                    _, ended = await server.call_tool(
                        "combat_end_turn",
                        {
                            "campaign_id": campaign["id"],
                            "actor_id": caster["id"],
                            "expected_revision": revision,
                            "idempotency_key": "end-first-caster",
                        },
                    )
                    revision = ended["campaign_revision"]
            before_restart = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                },
            )
            before_receipts = await _call(
                server,
                "campaign_rules",
                {
                    "campaign_id": campaign["id"],
                    "action": "receipts",
                    "payload": {},
                },
            )
            close_server(server)
            server = create_server(config)
            for cast_request, settled in cast_replays:
                _, replay = await server.call_tool("combat_cast_spell", cast_request)
                assert replay == settled
            assert (
                await _call(
                    server,
                    "campaign_query",
                    {
                        "view": "get",
                        "payload": {"campaign_id": campaign["id"]},
                    },
                )
                == before_restart
            )
            assert (
                await _call(
                    server,
                    "campaign_rules",
                    {
                        "campaign_id": campaign["id"],
                        "action": "receipts",
                        "payload": {},
                    },
                )
                == before_receipts
            )
            assert (
                await _call(
                    server,
                    "character_query",
                    {
                        "view": "get",
                        "payload": {"character_id": target["id"]},
                    },
                )
                == target_after
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
