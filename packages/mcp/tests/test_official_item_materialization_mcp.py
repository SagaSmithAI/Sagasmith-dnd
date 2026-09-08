"""Source-bound executable weapons from the locked official Eberron archive."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.official_item_materialization import (
    ARCANE_PROPULSION_ARM_ID,
    ARMBLADE_ID,
    DYRRN_TENTACLE_WHIP_ID,
    EBERRON_ITEM_PACK_ID,
)

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_official_expansions_mcp import _call, _locked_official_library

_VERSION = "1.0.8-local.infusion-source.2"
_SRD_LONGSWORD = "dnd5e.content.srd2014.item.longsword"


@pytest.mark.fresh_database
def test_locked_official_weapons_materialize_and_replay(tmp_path: Path) -> None:
    """Apply all reviewed weapon profiles through public tools and restart."""

    library = _locked_official_library()
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=True,
        official_content_library=library,
    )

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Executable official weapons",
                "edition": "2014",
                "idempotency_key": "campaign",
            })
            profile = await _call(server, "campaign_rules", {
                "campaign_id": campaign["id"],
                "action": "get_profile",
            })
            await _call(server, "content_pack", {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": EBERRON_ITEM_PACK_ID + ".addon",
                    "version": _VERSION,
                },
                "expected_revision": profile["campaign_revision"],
                "idempotency_key": "activate",
            })
            for artifact_id in (
                ARCANE_PROPULSION_ARM_ID,
                DYRRN_TENTACLE_WHIP_ID,
                ARMBLADE_ID,
            ):
                entries = await _call(server, "character_query", {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "query": artifact_id,
                        "include_context": True,
                    },
                })
                assert len(entries) == 1
                context = entries[0]["runtime_context"]
                assert context["executable_item_profile"]["artifact_id"] == artifact_id
                assert context["content_hash"] == context["catalog_review_hash"]

            actor_sheet = default_character_sheet()
            actor_sheet["progression"]["species"] = "Warforged"
            actor_sheet["traits"]["anatomy"] = {
                "functional_arms": 1,
                "functional_hands": 1,
            }
            actor = await _call(server, "character_create_from", {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Weapon profile tester",
                    "sheet": actor_sheet,
                },
                "idempotency_key": "actor",
            })

            async def apply(artifact_id: str, selection: dict | None, key: str) -> dict:
                nonlocal actor
                request = {
                    "character_id": actor["id"],
                    "artifact_id": artifact_id,
                    "selection": selection or {},
                    "expected_revision": actor["revision"],
                    "idempotency_key": key,
                }
                actor = await _call(server, "character_content_apply", request)
                assert (
                    actor["content_context"]["executable_item_profile"]["artifact_id"]
                    == artifact_id
                )
                assert await _call(server, "character_content_apply", request) == actor
                return actor

            await apply(ARCANE_PROPULSION_ARM_ID, None, "arcane")
            arcane = next(item for item in actor["sheet"]["inventory"]["items"]
                          if item["source_key"].endswith(":" + ARCANE_PROPULSION_ARM_ID))
            assert arcane["attunement"] == "required"
            assert arcane["mechanics"] == {
                "category": "other", "attack_type": "melee", "attack_ability": "strength",
                "damage_formula": "1d8", "damage_type": "force", "properties": ["thrown"],
                "normal_range_ft": 0, "long_range_ft": 0, "thrown_normal_range_ft": 20,
                "thrown_long_range_ft": 60, "proficient": True, "magical": True, "magic_bonus": 0,
                "official_item": {
                    "kind": "arcane_propulsion_arm", "state": "attached",
                    "qualification": "missing_hand_or_arm", "return_on_throw": True,
                    "remove_action": True,
                },
            }

            await apply(DYRRN_TENTACLE_WHIP_ID, None, "dyrrn")
            whip = next(item for item in actor["sheet"]["inventory"]["items"]
                        if item["source_key"].endswith(":" + DYRRN_TENTACLE_WHIP_ID))
            assert whip["attunement"] == "required"
            assert whip["mechanics"]["magic_bonus"] == 2
            assert whip["mechanics"]["additional_damage"] == [
                {"damage_formula": "1d6", "damage_type": "psychic", "damage_bonus": 0},
            ]
            assert whip["mechanics"]["properties"] == ["finesse", "reach"]
            assert whip["mechanics"]["official_item"]["natural_20_stun"] is True
            assert "stunned until the end of its next turn" in whip["mechanics"]["on_hit_effect"]

            pending_request = {
                "character_id": actor["id"],
                "artifact_id": ARMBLADE_ID,
                "selection": {},
                "expected_revision": actor["revision"],
                "idempotency_key": "armblade-pending",
            }
            pending = await _call(server, "character_content_apply", pending_request)
            assert pending["status"] == "pending_choice"
            assert actor["revision"] == pending["character_revision"]
            with pytest.raises(ToolError, match="active SRD 2014 core weapon"):
                await _call(server, "character_content_apply", {
                    **pending_request,
                    "selection": {"base_weapon_artifact_id": ARCANE_PROPULSION_ARM_ID},
                    "idempotency_key": "armblade-noncore",
                })
            with pytest.raises(ToolError, match="one-handed melee weapon"):
                await _call(server, "character_content_apply", {
                    **pending_request,
                    "selection": {
                        "base_weapon_artifact_id": "dnd5e.content.srd2014.item.greatsword",
                    },
                    "idempotency_key": "armblade-two-handed",
                })
            await apply(ARMBLADE_ID, {"base_weapon_artifact_id": _SRD_LONGSWORD}, "armblade")
            armblade = next(item for item in actor["sheet"]["inventory"]["items"]
                            if item["source_key"].endswith(":" + ARMBLADE_ID))
            assert armblade["name"] == "Armblade"
            assert armblade["attunement"] == "required"
            assert armblade["mechanics"]["magical"] is True
            assert armblade["mechanics"]["damage_formula"] == "1d8"
            assert "two-handed" not in armblade["mechanics"].get("properties", [])
            armblade_selection = next(
                item for item in actor["sheet"]["content"]["selections"]
                if item["artifact_id"] == ARMBLADE_ID
            )
            base_source = armblade_selection["selection"]["base_weapon_source"]
            assert base_source["artifact_id"] == _SRD_LONGSWORD
            assert base_source["pack_id"] == "dnd5e.content.srd2014"
            assert base_source["content_hash"] == (
                actor["content_context"]["executable_item_profile"]["base_weapon_source"]
                ["content_hash"]
            )
            receipt = next(
                item for item in actor["rule_receipts"]
                if item["artifact_id"] == ARMBLADE_ID
            )
            assert receipt["base_weapon_source"] == base_source
            final = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            restored = await _call(restarted, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            assert restored == final
        finally:
            close_server(restarted)

    asyncio.run(exercise())
