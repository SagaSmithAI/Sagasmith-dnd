import asyncio
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
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
        for slug in ("city-watch", "investigator"):
            artifact = next(
                item
                for item in package["content"]["artifacts"]
                if item["id"] == f"{definition_id}.background.{slug}"
            )
            source = next(
                item
                for item in artifact["source_refs"]
                if item["note"].endswith("Watcher's Eye feature evidence")
            )
            assert source["page"] == 146
            assert "/section-613/" in source["chunk_key"]
            assert any(
                ref.endswith(f"#chunk:{source['chunk_key']}") for ref in artifact["rule_refs"]
            )
            excerpt = " ".join(
                str(item.get("source_excerpt", ""))
                for item in artifact["card"]["ruling_requirements"]
            )
            assert "find the local outpost" in excerpt
            assert "dens of criminal activity" in excerpt
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

        # Exercise each source-defined knowledge category, including a missing
        # fact and an explicitly unavailable campaign fact, through the real pack.
        for capability in ("local_law", "local_criminal_activity", "watch_outpost"):
            for outcome in ("pending_gm_ruling", "granted", "unavailable"):
                fact_key = f"location:waterdeep:{capability}:{outcome}"
                current = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                if outcome != "pending_gm_ruling":
                    await _call(
                        server,
                        "memory_change",
                        {
                            "campaign_id": campaign["id"],
                            "action": "upsert",
                            "payload": {
                                **fact,
                                "fact_key": fact_key,
                                "predicate": f"dnd5e.watchers_eye.{capability}",
                                "content": f"Campaign-authored {capability}: {outcome}.",
                                "metadata": {
                                    "dnd5e_watchers_eye": {
                                        "schema_version": 1,
                                        "capability": capability,
                                        "outcome": outcome,
                                    }
                                },
                            },
                            "expected_revision": current["revision"],
                            "idempotency_key": f"fact:{capability}:{outcome}",
                        },
                    )
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
                            "capability": capability,
                            "settlement_ref": "location:waterdeep",
                            "fact_key": fact_key,
                        },
                        "expected_revision": current["revision"],
                        "idempotency_key": f"settle:{actor['id']}:{capability}:{outcome}",
                    }
                    _, response = await server.call_tool("character_check", args)
                    result = response["result"]
                    assert result["outcome"] == outcome
                    assert bool(result["fact_revision_id"]) == (outcome != "pending_gm_ruling")
                    receipt = response["rule_receipts"][0]
                    assert receipt["artifact_id"] == feature["choices"]["narrative_capability"][
                        "source_binding"
                    ]["artifact_id"]
                    assert receipt["capability"] == capability
                    assert receipt["outcome"] == outcome
                    assert receipt["addon_checksum"] == package["checksum"]
                    assert "bonus" not in result
                    assert "dc" not in result
                    assert await _call(server, "character_check", args) == result
                    calls.append((args, result))

        async def assert_rejected_without_mutation(args: dict, message: str) -> None:
            before_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[0][0]["id"]}},
            )
            before_receipts = await _call(
                server, "campaign_rules", {"campaign_id": campaign["id"], "action": "receipts"}
            )
            with pytest.raises(ToolError, match=message):
                await _call(server, "character_check", args)
            assert await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            ) == before_campaign
            assert await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[0][0]["id"]}},
            ) == before_actor
            assert await _call(
                server, "campaign_rules", {"campaign_id": campaign["id"], "action": "receipts"}
            ) == before_receipts

        # JSON boolean/float equality with integer 1 must not satisfy a versioned
        # source-fact contract or create a capability receipt.
        for index, invalid_version in enumerate((True, 1.0)):
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            malformed_fact = {
                **fact,
                "fact_key": f"location:waterdeep:malformed:{index}",
                "metadata": {
                    "dnd5e_watchers_eye": {
                        "schema_version": invalid_version,
                        "capability": "watch_outpost",
                        "outcome": "granted",
                    }
                },
            }
            await _call(
                server,
                "memory_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "upsert",
                    "payload": malformed_fact,
                    "expected_revision": current["revision"],
                    "idempotency_key": f"malformed-fact:{index}",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            args = {
                "campaign_id": campaign["id"],
                "action": "source_feature",
                "payload": {
                    "actor_id": actors[0][0]["id"],
                    "feature_id": actors[0][1]["id"],
                    "capability": "watch_outpost",
                    "settlement_ref": "location:waterdeep",
                    "fact_key": malformed_fact["fact_key"],
                },
                "expected_revision": current["revision"],
                "idempotency_key": f"reject-malformed:{index}",
            }
            await assert_rejected_without_mutation(args, "source-fact contract")
        await assert_rejected_without_mutation(
            {**args, "expected_revision": current["revision"] - 1, "idempotency_key": "stale-cas"},
            "revision conflict",
        )
        await assert_rejected_without_mutation(
            {
                **args,
                "payload": {**args["payload"], "capability": "numeric_bonus"},
                "idempotency_key": "reject-numeric-bonus",
            },
            "capability must be one of",
        )
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
