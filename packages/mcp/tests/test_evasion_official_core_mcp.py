from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd import character_schema
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp import server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
@pytest.mark.parametrize("edition", ["2014", "2024"])
@pytest.mark.parametrize("class_name", ["Monk", "Rogue"])
def test_real_core_evasion_is_applied_to_level_seven_classes(
    tmp_path: Path, edition: str, class_name: str
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
        finally:
            close_server(server)

    asyncio.run(exercise())
