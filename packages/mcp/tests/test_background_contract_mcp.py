import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.content_validation import build_selection_contract

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import import_and_activate_addon_fixture


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _artifact(artifact_id: str, kind: str, card: dict) -> dict:
    artifact = {
        "id": artifact_id,
        "kind": kind,
        "application_state": "selection_ready",
        "mechanical_scope": "descriptive",
        "execution_state": "descriptive_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "descriptive",
            "first_use_compilation_required": False,
        },
        "card": card,
        "rule_refs": [f"book:fixture:{artifact_id}"],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=artifact["rule_refs"],
    )
    return artifact


def _background_artifacts() -> list[dict]:
    equipment = {
        "items": [
            {
                "inventory_template": {
                    "name": "Watch Uniform",
                    "kind": "equipment",
                    "quantity": 1,
                    "description": "Reviewed City Watch fixture equipment.",
                    "mechanics": {},
                }
            }
        ],
        "wallet": {"gp": 10},
    }
    city_watch = _artifact(
        "dnd5e.addon.background-contract.background.city-watch",
        "background",
        {
            "name": "City Watch",
            "skill_proficiencies": ["athletics", "insight"],
            "background_grants": {
                "skills": ["athletics", "insight"],
                "feature": "Watcher's Eye",
                "languages": [],
                "tools": [],
                "spell_list_expansion": [],
                "equipment_item_ids": [],
                "equipment": equipment,
                "choices": {
                    "language_count": 2,
                    "allow_any_language": True,
                    "skill_choice_count": 0,
                    "tool_choice_count": 0,
                },
            },
        },
    )
    hermit = _artifact(
        "dnd5e.addon.background-contract.background.hermit",
        "background",
        {
            "name": "Hermit",
            "skill_proficiencies": ["medicine", "religion"],
            "background_grants": {
                "skills": ["medicine", "religion"],
                "feature": "Discovery",
                "languages": [],
                "tools": [],
                "spell_list_expansion": [],
                "equipment_item_ids": [],
                "equipment": equipment,
                "choices": {
                    "language_count": 1,
                    "allow_any_language": True,
                    "skill_choice_count": 0,
                    "tool_choice_count": 0,
                },
            },
        },
    )
    unready_feature = _artifact(
        "dnd5e.addon.background-contract.background.unready-feature",
        "background",
        {
            "name": "Unready Feature",
            "background_grants": {"feature": "Unreviewed Feature"},
        },
    )
    unready_feature["application_state"] = "review_required"
    criminal = _artifact(
        "dnd5e.addon.background-contract.background.criminal",
        "background",
        {
            "name": "Criminal",
            "skill_proficiencies": ["deception", "stealth"],
            "background_grants": {
                "skills": ["deception", "stealth"],
                "feature": "Criminal Contact",
                "languages": [],
                "tools": ["Thieves' Tools"],
                "spell_list_expansion": [],
                "equipment_item_ids": [],
                "equipment": equipment,
                "choices": {
                    "language_count": 0,
                    "skill_choice_count": 0,
                    "tool_choice_count": 0,
                },
            },
        },
    )
    self_duplicate = _artifact(
        "dnd5e.addon.background-contract.background.self-duplicate",
        "background",
        {
            "name": "Self Duplicate",
            "skill_proficiencies": ["athletics"],
            "background_grants": {
                "skills": ["athletics"],
                "feature": "Source Collision",
                "languages": [],
                "tools": ["Thieves' Tools"],
                "spell_list_expansion": [],
                "equipment_item_ids": [],
                "equipment": equipment,
                "choices": {
                    "language_count": 0,
                    "skill_choice_count": 1,
                    "skill_options": ["athletics", "arcana"],
                    "tool_choice_count": 1,
                    "tool_options": ["Thieves' Tools", "Forgery Kit"],
                },
            },
        },
    )
    fighter = _artifact(
        "dnd5e.addon.background-contract.class.fighter",
        "class",
        {
            "name": "Fighter",
            "class_definition": {
                "hit_die": 10,
                "saving_throw_proficiencies": ["strength", "constitution"],
                "armor_proficiencies": ["all armor", "shields"],
                "weapon_proficiencies": ["simple weapons", "martial weapons"],
                "tool_proficiencies": [],
                "skill_choice_count": 2,
                "skill_options": ["athletics", "perception", "survival"],
            },
        },
    )
    return [
        city_watch,
        hermit,
        unready_feature,
        criminal,
        self_duplicate,
        fighter,
    ]


def _config(tmp_path: Path) -> McpConfig:
    workspace = Path(__file__).resolve().parents[3]
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )


async def _setup(server, config: McpConfig) -> tuple[dict, list[dict]]:
    campaign = await _call(
        server,
        "campaign_create",
        {"name": "2014 background contracts", "idempotency_key": "background-contract"},
    )
    profile = await _call(
        server,
        "campaign_rules",
        {
            "campaign_id": campaign["id"],
            "action": "set_profile",
            "payload": {"edition": "2014"},
            "principal_id": "system:local",
            "expected_revision": campaign["revision"],
            "idempotency_key": "background-contract-profile",
        },
    )
    artifacts = _background_artifacts()
    activation = await import_and_activate_addon_fixture(
        _call,
        server,
        campaign["id"],
        config.home,
        manifest={
            "id": "dnd5e.addon.background-contract",
            "version": "1.0.0",
            "title": "Reviewed 2014 background contract fixture",
            "namespace": "dnd5e.addon.background-contract",
            "system_id": "dnd5e",
            "editions": ["2014"],
            "capabilities": [],
        },
        artifacts=artifacts,
        mechanics=[],
        expected_revision=profile["campaign_revision"],
        request_key="background-contract",
    )
    campaign["revision"] = activation["campaign_revision"]
    return campaign, artifacts


async def _create(server, campaign_id: str, key: str, sheet: dict | None = None) -> dict:
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {
                "campaign_id": campaign_id,
                "name": key,
                **({"sheet": sheet} if sheet is not None else {}),
            },
            "principal_id": "system:local",
            "idempotency_key": f"create:{key}",
        },
    )


@pytest.mark.fresh_database
def test_2014_background_duplicates_languages_customization_and_order(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def exercise() -> None:
        server = create_server(config)
        campaign, artifacts = await _setup(server, config)
        (
            city_watch,
            hermit,
            unready_feature,
            criminal,
            self_duplicate,
            fighter,
        ) = artifacts

        catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "background",
                    "query": city_watch["id"],
                },
            },
        )
        requirements = catalog[0]["selection_requirements"]
        assert requirements["custom_contract"] == {
            "skill_count": 2,
            "combined_tool_or_language_count": 2,
            "equipment_modes": ["source", "starting_coin"],
            "equipment_modes_are_mutually_exclusive": True,
        }
        assert requirements["allowed_language_catalog"] == [
            "Common",
            "Dwarvish",
            "Elvish",
            "Giant",
            "Gnomish",
            "Goblin",
            "Halfling",
            "Orc",
        ]

        expert_sheet = default_character_sheet()
        expert_sheet["skills"]["athletics"]["proficiency"] = "expertise"
        expert = await _create(server, campaign["id"], "expert", expert_sheet)
        pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": expert["id"],
                "artifact_id": city_watch["id"],
                "selection": {"languages": ["Elvish", "Goblin"]},
                "expected_revision": expert["revision"],
                "idempotency_key": "expert:pending",
            },
        )
        assert pending["status"] == "pending_choice"
        assert "athletics" in pending["reason"]
        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": expert["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "languages": ["Elvish", "Goblin"],
                    "skill_replacements": {"ATHLETICS": "Arcana"},
                },
                "expected_revision": expert["revision"],
                "idempotency_key": "expert:apply",
            },
        )
        assert applied["sheet"]["skills"]["athletics"]["proficiency"] == "expertise"
        assert applied["sheet"]["skills"]["arcana"]["proficiency"] == "proficient"
        assert applied["sheet"]["skills"]["insight"]["proficiency"] == "proficient"
        receipt_selection = applied["sheet"]["content"]["selections"][0]["selection"]
        assert receipt_selection["skill_replacements"] == {"athletics": "arcana"}
        assert receipt_selection["_background_authority"]["authorization"]["signature"]

        replay = await _call(
            server,
            "character_content_apply",
            {
                "character_id": expert["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "languages": ["Elvish", "Goblin"],
                    "skill_replacements": {"ATHLETICS": "Arcana"},
                },
                "expected_revision": expert["revision"],
                "idempotency_key": "expert:apply",
            },
        )
        assert replay == applied

        background_first = await _create(server, campaign["id"], "background-first")
        background_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": background_first["id"],
                "artifact_id": city_watch["id"],
                "selection": {"languages": ["Elvish", "Goblin"]},
                "expected_revision": background_first["revision"],
                "idempotency_key": "background-first:background",
            },
        )
        class_pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": background_first["id"],
                "artifact_id": fighter["id"],
                "selection": {"skills": ["athletics", "perception"]},
                "expected_revision": background_applied["revision"],
                "idempotency_key": "background-first:class-pending",
            },
        )
        assert class_pending["status"] == "pending_choice"
        class_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": background_first["id"],
                "artifact_id": fighter["id"],
                "selection": {
                    "skills": ["perception", "athletics"],
                    "skill_replacements": {"athletics": "survival"},
                },
                "expected_revision": background_applied["revision"],
                "idempotency_key": "background-first:class",
            },
        )
        assert class_applied["sheet"]["skills"]["athletics"]["proficiency"] == "proficient"
        assert class_applied["sheet"]["skills"]["survival"]["proficiency"] == "proficient"

        class_first = await _create(server, campaign["id"], "class-first")
        class_first_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": class_first["id"],
                "artifact_id": fighter["id"],
                "selection": {"skills": ["athletics", "perception"]},
                "expected_revision": class_first["revision"],
                "idempotency_key": "class-first:class",
            },
        )
        background_pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": class_first["id"],
                "artifact_id": city_watch["id"],
                "selection": {"languages": ["Elvish", "Goblin"]},
                "expected_revision": class_first_applied["revision"],
                "idempotency_key": "class-first:background-pending",
            },
        )
        assert background_pending["status"] == "pending_choice"
        background_second = await _call(
            server,
            "character_content_apply",
            {
                "character_id": class_first["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "languages": ["Elvish", "Goblin"],
                    "skill_replacements": {"athletics": "arcana"},
                },
                "expected_revision": class_first_applied["revision"],
                "idempotency_key": "class-first:background",
            },
        )
        assert background_second["sheet"]["skills"]["athletics"]["proficiency"] == "proficient"
        assert background_second["sheet"]["skills"]["insight"]["proficiency"] == "proficient"
        assert background_second["sheet"]["skills"]["arcana"]["proficiency"] == "proficient"

        tool_sheet = default_character_sheet()
        tool_sheet["traits"]["proficiencies"]["tools"] = ["thieves' tools"]
        tool_actor = await _create(server, campaign["id"], "tool-duplicate", tool_sheet)
        tool_pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": tool_actor["id"],
                "artifact_id": criminal["id"],
                "expected_revision": tool_actor["revision"],
                "idempotency_key": "tool:pending",
            },
        )
        assert tool_pending["status"] == "pending_choice"
        tool_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": tool_actor["id"],
                "artifact_id": criminal["id"],
                "selection": {"tool_replacements": {"THIEVES' TOOLS": "Herbalism Kit"}},
                "expected_revision": tool_actor["revision"],
                "idempotency_key": "tool:apply",
            },
        )
        assert tool_applied["sheet"]["traits"]["proficiencies"]["tools"] == [
            "thieves' tools",
            "Herbalism Kit",
        ]

        self_actor = await _create(server, campaign["id"], "same-source-duplicate")
        self_tool_pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": self_actor["id"],
                "artifact_id": self_duplicate["id"],
                "selection": {
                    "skills": ["athletics"],
                    "tools": ["Thieves' Tools"],
                },
                "expected_revision": self_actor["revision"],
                "idempotency_key": "same-source:tool-pending",
            },
        )
        assert "tool proficiency replacements" in self_tool_pending["reason"]
        self_skill_pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": self_actor["id"],
                "artifact_id": self_duplicate["id"],
                "selection": {
                    "skills": ["athletics"],
                    "tools": ["Thieves' Tools"],
                    "tool_replacements": {"thieves' tools": "Herbalism Kit"},
                },
                "expected_revision": self_actor["revision"],
                "idempotency_key": "same-source:skill-pending",
            },
        )
        assert "skill proficiency replacements" in self_skill_pending["reason"]
        self_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": self_actor["id"],
                "artifact_id": self_duplicate["id"],
                "selection": {
                    "skills": ["athletics"],
                    "tools": ["Thieves' Tools"],
                    "skill_replacements": {"athletics": "arcana"},
                    "tool_replacements": {"thieves' tools": "Herbalism Kit"},
                },
                "expected_revision": self_actor["revision"],
                "idempotency_key": "same-source:apply",
            },
        )
        assert self_applied["sheet"]["skills"]["athletics"]["proficiency"] == "proficient"
        assert self_applied["sheet"]["skills"]["arcana"]["proficiency"] == "proficient"
        assert self_applied["sheet"]["traits"]["proficiencies"]["tools"] == [
            "Thieves' Tools",
            "Herbalism Kit",
        ]

        restricted = await _create(server, campaign["id"], "restricted-language")
        with pytest.raises(Exception, match="language_authorization requires exactly"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": restricted["id"],
                    "artifact_id": city_watch["id"],
                    "selection": {"languages": ["Elvish", "Celestial"]},
                    "expected_revision": restricted["revision"],
                    "idempotency_key": "language:missing-auth",
                },
            )
        restricted_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": restricted["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "languages": ["Elvish", "Celestial"],
                    "language_authorization": {
                        "languages": ["celestial"],
                        "reason": "The DM approved this source-specific language.",
                    },
                },
                "expected_revision": restricted["revision"],
                "idempotency_key": "language:authorized",
            },
        )
        language_receipt = restricted_applied["sheet"]["progression"]["background_grants"][
            "choices"
        ]["language_authorization"]
        assert language_receipt["principal_id"] == "system:local"
        assert language_receipt["languages"] == ["Celestial"]

        campaign_specific = await _create(server, campaign["id"], "campaign-specific-language")
        await _call(
            server,
            "access_grant",
            {
                "scope": "campaign",
                "campaign_id": campaign["id"],
                "principal_id": "player:language-selector",
                "payload": {"role": "player"},
            },
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "actor",
                "campaign_id": campaign["id"],
                "principal_id": "player:language-selector",
                "payload": {"actor_id": campaign_specific["id"], "can_control": True},
            },
        )
        with pytest.raises(Exception, match="require the DM"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": campaign_specific["id"],
                    "artifact_id": city_watch["id"],
                    "selection": {
                        "languages": ["Elvish", "High Netherese"],
                        "language_authorization": {
                            "languages": ["High Netherese"],
                            "reason": "A player cannot self-authorize this language.",
                        },
                    },
                    "principal_id": "player:language-selector",
                    "expected_revision": campaign_specific["revision"],
                    "idempotency_key": "language:campaign-specific-player",
                },
            )
        campaign_specific_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": campaign_specific["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "languages": ["Elvish", "High Netherese"],
                    "language_authorization": {
                        "languages": ["high netherese"],
                        "reason": "The DM approved this campaign-specific language.",
                    },
                },
                "expected_revision": campaign_specific["revision"],
                "idempotency_key": "language:campaign-specific-authorized",
            },
        )
        campaign_language_receipt = campaign_specific_applied["sheet"]["progression"][
            "background_grants"
        ]["choices"]["language_authorization"]
        assert campaign_language_receipt["languages"] == ["High Netherese"]

        existing_language_sheet = default_character_sheet()
        existing_language_sheet["traits"]["languages"] = ["Common"]
        existing_language = await _create(
            server,
            campaign["id"],
            "existing-language",
            existing_language_sheet,
        )
        with pytest.raises(Exception, match="background languages must be new"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": existing_language["id"],
                    "artifact_id": city_watch["id"],
                    "selection": {"languages": ["common", "Elvish"]},
                    "expected_revision": existing_language["revision"],
                    "idempotency_key": "language:existing-casefold",
                },
            )

        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        updated_campaign = await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "update",
                "payload": {
                    "settings": {
                        "language_catalog": {
                            "allowed_languages": [
                                "Common",
                                "Dwarvish",
                                "Elvish",
                                "Giant",
                                "Gnomish",
                                "Goblin",
                                "Halfling",
                                "Orc",
                                "High Netherese",
                                "Celestial",
                            ]
                        }
                    }
                },
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "campaign-language-catalog",
            },
        )
        catalog_actor = await _create(server, campaign["id"], "catalog-language")
        catalog_language = await _call(
            server,
            "character_content_apply",
            {
                "character_id": catalog_actor["id"],
                "artifact_id": city_watch["id"],
                "selection": {"languages": ["High Netherese", "Elvish"]},
                "expected_revision": catalog_actor["revision"],
                "idempotency_key": "language:campaign-catalog",
            },
        )
        assert catalog_language["sheet"]["traits"]["languages"] == [
            "High Netherese",
            "Elvish",
        ]
        assert updated_campaign["settings"]["language_catalog"]["allowed_languages"][-1] == (
            "Celestial"
        )

        custom_actor = await _create(server, campaign["id"], "custom-source")
        custom = await _call(
            server,
            "character_content_apply",
            {
                "character_id": custom_actor["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "custom_name": "Watch Physician",
                    "custom_feature_artifact_id": hermit["id"],
                    "skills": ["medicine", "religion"],
                    "tools": ["Herbalism Kit"],
                    "languages": ["Elvish"],
                    "equipment_mode": "source",
                },
                "expected_revision": custom_actor["revision"],
                "idempotency_key": "custom:source",
            },
        )
        custom_grants = custom["sheet"]["progression"]["background_grants"]
        assert custom["sheet"]["progression"]["background"] == "Watch Physician"
        assert custom_grants["feature"] == "Discovery"
        assert custom_grants["choices"]["feature_source_artifact_id"] == hermit["id"]
        feature_source = custom_grants["choices"]["feature_source"]
        assert feature_source["artifact_id"] == hermit["id"]
        assert feature_source["pack_id"] == "dnd5e.addon.background-contract"
        assert feature_source["pack_version"] == "1.0.0"
        assert len(feature_source["content_hash"]) == 64
        assert custom_grants["choices"]["equipment_mode"] == "source"
        assert len(custom_grants["equipment_item_ids"]) == 1
        assert custom["sheet"]["inventory"]["wallet"]["gp"] == 10

        unready_actor = await _create(server, campaign["id"], "custom-unready")
        with pytest.raises(Exception, match="not selection-ready"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": unready_actor["id"],
                    "artifact_id": city_watch["id"],
                    "selection": {
                        "custom_name": "Unready Route",
                        "custom_feature_artifact_id": unready_feature["id"],
                        "skills": ["medicine", "religion"],
                        "languages": ["Elvish"],
                        "tools": ["Herbalism Kit"],
                        "equipment_mode": "source",
                    },
                    "expected_revision": unready_actor["revision"],
                    "idempotency_key": "custom:unready",
                },
            )
        unchanged = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": unready_actor["id"]},
            },
        )
        assert unchanged["revision"] == unready_actor["revision"]
        assert not unchanged["sheet"]["progression"]["background_grants"]["feature"]

        coin_actor = await _create(server, campaign["id"], "custom-coin")
        coin = await _call(
            server,
            "character_content_apply",
            {
                "character_id": coin_actor["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "custom_name": "Coin Route",
                    "skills": ["medicine", "religion"],
                    "languages": ["Elvish", "Goblin"],
                    "equipment_mode": "starting_coin",
                },
                "expected_revision": coin_actor["revision"],
                "idempotency_key": "custom:coin",
            },
        )
        assert coin["sheet"]["inventory"]["items"] == []
        assert coin["sheet"]["inventory"]["wallet"]["gp"] == 0
        assert (
            coin["sheet"]["progression"]["background_grants"]["choices"]["equipment_mode"]
            == "starting_coin"
        )

        stacked_actor = await _create(server, campaign["id"], "custom-stacked")
        with pytest.raises(Exception, match="cannot stack source equipment"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": stacked_actor["id"],
                    "artifact_id": city_watch["id"],
                    "selection": {
                        "custom_name": "Stacked Route",
                        "skills": ["medicine", "religion"],
                        "languages": ["Elvish", "Goblin"],
                        "equipment_mode": "starting_coin",
                        "equipment_item_ids": ["caller-injected-item"],
                    },
                    "expected_revision": stacked_actor["revision"],
                    "idempotency_key": "custom:stacked",
                },
            )

        copied_actor = await _create(server, campaign["id"], "copied-authority")
        with pytest.raises(Exception, match="does not match the actor state"):
            await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": copied_actor["id"],
                    "sheet": applied["sheet"],
                    "expected_revision": copied_actor["revision"],
                    "idempotency_key": "authority:copy-other-actor",
                },
            )
        tampered = deepcopy(applied["sheet"])
        tampered["progression"]["background_grants"]["feature"] = "Forged Feature"
        with pytest.raises(Exception, match="does not match the actor state"):
            await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": expert["id"],
                    "sheet": tampered,
                    "expected_revision": applied["revision"],
                    "idempotency_key": "authority:tamper",
                },
            )

        race_actor = await _create(server, campaign["id"], "race")
        race_calls = [
            _call(
                server,
                "character_content_apply",
                {
                    "character_id": race_actor["id"],
                    "artifact_id": city_watch["id"],
                    "selection": {"languages": languages},
                    "expected_revision": race_actor["revision"],
                    "idempotency_key": f"race:{index}",
                },
            )
            for index, languages in enumerate(
                (["Elvish", "Goblin"], ["Dwarvish", "Giant"]), start=1
            )
        ]
        race_results = await asyncio.gather(*race_calls, return_exceptions=True)
        assert sum(isinstance(item, dict) for item in race_results) == 1
        assert sum("revision conflict" in str(item) for item in race_results) == 1

        close_server(server)
        restarted = create_server(config)
        persisted = await _call(
            restarted,
            "character_query",
            {"view": "get", "payload": {"character_id": expert["id"]}},
        )
        assert persisted["sheet"]["content"]["selections"][0]["selection"] == receipt_selection
        close_server(restarted)

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_background_grants_are_rejected_at_whole_sheet_build_and_content_actor_ingress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)

    async def exercise() -> None:
        server = create_server(config)
        campaign, _artifacts = await _setup(server, config)
        forged = default_character_sheet()
        forged["progression"]["background"] = "Forged"
        forged["progression"]["background_grants"]["feature"] = "Forged Feature"

        for mode in ("direct", "build"):
            with pytest.raises(Exception, match="only by character_content_apply"):
                await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": mode,
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": f"forged-{mode}",
                            "sheet": forged,
                        },
                        "idempotency_key": f"forged:{mode}",
                    },
                )

        clean = await _create(server, campaign["id"], "whole-sheet")
        with pytest.raises(Exception, match="authoritative background state requires"):
            await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": clean["id"],
                    "sheet": forged,
                    "expected_revision": clean["revision"],
                    "idempotency_key": "forged:replace",
                },
            )

        original_validate_actor = server_module.validate_dnd_content_actor

        def forge_validated_actor(card):
            forged_card = original_validate_actor(card)
            forged_card["sheet"]["progression"]["background"] = "Forged"
            forged_card["sheet"]["progression"]["background_grants"]["feature"] = "Forged Feature"
            return forged_card

        monkeypatch.setattr(
            server_module,
            "validate_dnd_content_actor",
            forge_validated_actor,
        )
        with pytest.raises(Exception, match="only by character_content_apply"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "content_actor",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "artifact_id": ("dnd5e.presets.srd2014.actors.actor.dfea48e164b5720d8d48"),
                    },
                    "idempotency_key": "forged:content-actor",
                },
            )
        close_server(server)

    asyncio.run(exercise())
