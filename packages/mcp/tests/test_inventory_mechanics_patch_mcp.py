import asyncio

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


def test_partial_weapon_update_preserves_mechanics_and_replays(tmp_path):
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )

    async def exercise():
        server = create_server(config)

        async def call(name, arguments):
            _, response = await server.call_tool(name, arguments)
            return response.get("result", response)

        try:
            campaign = await call(
                "campaign_create", {"name": "Inventory patch", "idempotency_key": "campaign"}
            )
            actor = await call(
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Archer"},
                    "idempotency_key": "actor",
                },
            )
            for item in [
                {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 20},
                {
                    "id": "bow",
                    "name": "Shortbow",
                    "kind": "weapon",
                    "mechanics": {
                        "category": "simple",
                        "attack_type": "ranged",
                        "attack_ability": "dexterity",
                        "damage_formula": "1d6",
                        "damage_type": "piercing",
                        "normal_range_ft": 80,
                        "long_range_ft": 320,
                        "properties": ["ammunition", "two-handed"],
                    },
                },
            ]:
                added = await call(
                    "inventory_change",
                    {
                        "owner": "character",
                        "action": "add",
                        "owner_id": actor["id"],
                        "payload": {"item": item},
                        "expected_revision": actor["revision"],
                        "idempotency_key": item["id"],
                    },
                )
                actor = added["character"]
            before = actor["sheet"]["inventory"]["items"][1]["mechanics"]
            arguments = {
                "owner": "character",
                "action": "update",
                "owner_id": actor["id"],
                "payload": {
                    "item_id": "bow",
                    "patch": {"mechanics": {"ammunition_item_id": "arrows"}},
                },
                "expected_revision": actor["revision"],
                "idempotency_key": "bind",
            }
            updated = await call("inventory_change", arguments)
            assert updated["sheet"]["inventory"]["items"][1]["mechanics"] == {
                **before,
                "ammunition_item_id": "arrows",
            }
            assert updated["revision"] == actor["revision"] + 1
            assert await call("inventory_change", arguments) == updated
        finally:
            close_server(server)

    asyncio.run(exercise())
