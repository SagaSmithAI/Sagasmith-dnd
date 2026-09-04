import asyncio
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
def test_scag_103_official_archive_activates_and_applies_watchers_eye(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        archive_root = workspace.parent / "SagaSmith-dnd-content-library" / "content-library"
        if not (archive_root / "index.json").is_file():
            pytest.skip("requires the sibling finalized content library")
        archives = list(
            archive_root.glob("packages/*sword-coast-adventurer-s-guide*1.0.3.sagasmith-pack")
        )
        assert len(archives) == 1
        with zipfile.ZipFile(archives[0]) as archive:
            package = json.loads(archive.read("package.sagasmith.json"))
        definition_id = package["content"]["rule_definitions"][0]["id"]
        config = replace(
            _config(tmp_path),
            official_content_library=archive_root,
            auto_seed_rules=True,
            dnd_skills_dir=workspace / "skills",
        )
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Official SCAG", "edition": "2014", "idempotency_key": "campaign"},
        )
        profile = await _call(
            server, "campaign_rules", {"campaign_id": campaign["id"], "action": "get_profile"}
        )
        activated = await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": package["id"],
                    "version": package["version"],
                },
                "expected_revision": profile["campaign_revision"],
                "idempotency_key": "activate-scag-103",
            },
        )
        assert activated["activation"]["version"] == "1.0.3"

        async def make_character(name: str) -> dict:
            sheet = default_character_sheet()
            if name == "City Watch":
                # With the source's 144 oz of equipment, this reaches exactly
                # STR 10's 800 oz threshold before the ten coins are counted.
                sheet["inventory"]["encumbrance"]["mode"] = "variant"
                sheet["inventory"]["items"].append(
                    {
                        "id": "threshold-load",
                        "name": "Threshold load",
                        "kind": "equipment",
                        "weight_oz": 656,
                    }
                )
            return await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": name,
                        "sheet": sheet,
                    },
                    "idempotency_key": f"official-scag-character:{name}",
                },
            )

        backgrounds = {
            "City Watch": (f"{definition_id}.background.city-watch", {"athletics", "insight"}),
            "Investigator": (
                f"{definition_id}.background.investigator",
                {"investigation", "insight"},
            ),
        }
        actors: list[tuple[dict, dict]] = []
        for name, (artifact_id, expected_skills) in backgrounds.items():
            actor = await make_character(name)
            if name == "City Watch":
                assert actor["derived"]["inventory"]["total_weight_oz"] == 656
                assert actor["derived"]["speed"]["walk"] == 30
            actor = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": actor["id"],
                    "artifact_id": artifact_id,
                    "selection": {"languages": ["Elvish", "Dwarvish"]},
                    "expected_revision": actor["revision"],
                    "idempotency_key": f"official-scag-apply:{name}",
                },
            )
            feature = next(
                item
                for item in actor["sheet"]["content"]["features"]
                if item["name"] == "Watcher's Eye"
            )
            proficient = {
                skill
                for skill, value in actor["sheet"]["skills"].items()
                if value["proficiency"] == "proficient"
            }
            assert proficient == expected_skills
            if name == "City Watch":
                assert actor["sheet"]["inventory"]["wallet"]["gp"] == 10
                assert actor["derived"]["inventory"]["total_weight_oz"] == pytest.approx(803.2)
                assert actor["derived"]["inventory"]["encumbrance"]["state"] == "encumbered"
                assert actor["derived"]["speed"]["walk"] == 20
            assert feature["pack_id"] == definition_id
            binding = feature["choices"]["narrative_capability"]["source_binding"]
            assert binding["addon_id"] == package["id"]
            assert binding["addon_checksum"] == package["checksum"]
            actors.append((actor, feature))

        fact = {
            "fact_key": "location:waterdeep:watch-outpost",
            "kind": "source_fact",
            "subject": "Waterdeep watch outpost",
            "subject_ref": "location:waterdeep",
            "predicate": "dnd5e.watchers_eye.watch_outpost",
            "content": "The campaign establishes one nearby watch outpost.",
            "metadata": {
                "dnd5e_watchers_eye": {
                    "schema_version": 1,
                    "capability": "watch_outpost",
                    "outcome": "granted",
                }
            },
            "disclosure_scope": "dm",
        }
        current = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": "official-scag-play",
            },
        )
        current = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "upsert",
                "payload": fact,
                "expected_revision": current["revision"],
                "idempotency_key": "official-scag-watch-outpost",
            },
        )
        current = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        calls: list[tuple[dict, dict]] = []
        for actor, feature in actors:
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            args = {
                "campaign_id": campaign["id"],
                "action": "source_feature",
                "payload": {
                    "actor_id": actor["id"],
                    "feature_id": feature["id"],
                    "capability": "watch_outpost",
                    "settlement_ref": "location:waterdeep",
                    "fact_key": fact["fact_key"],
                },
                "expected_revision": current["revision"],
                "idempotency_key": f"official-scag-settle:{actor['id']}",
            }
            result = await _call(server, "character_check", args)
            assert result["outcome"] == "granted"
            assert await _call(server, "character_check", args) == result
            calls.append((args, result))
        persisted_actors = [
            await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            for actor, _feature in actors
        ]
        close_server(server)
        server = create_server(config)
        for args, result in calls:
            assert await _call(server, "character_check", args) == result
        for before in persisted_actors:
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": before["id"]}},
            )
            assert after["revision"] == before["revision"]
            assert after["sheet"] == before["sheet"]
            assert after["derived"]["inventory"] == before["derived"]["inventory"]
            assert after["derived"]["speed"] == before["derived"]["speed"]
        close_server(server)

    asyncio.run(exercise())
