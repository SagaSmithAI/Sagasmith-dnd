import asyncio

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)

from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_readied_spell_release_mcp import ready
from tests.test_structured_spell_mcp import (
    _call,
    _campaign_actor_snapshot,
    _campaign_with_combat,
    _config,
    _equipped_caster,
    _raw,
    _slot,
    _spell,
)


def test_readied_spell_protection_commits_release_then_returns_exact_resume_request(tmp_path):
    async def run():
        config = _config(tmp_path)
        server = create_server(config)
        try:
            caster = _equipped_caster()
            spell = _spell("Scorching Ray", 2, casting_time="1 action", range_ft=120)
            caster["content"]["spells"] = [spell]
            caster["spellcasting"].update(ability="intelligence", spell_slots=_slot(2))
            target = default_character_sheet()
            target["combat"]["hp"].update(value=100, max=100)
            target["combat"]["ac"]["override"] = 1
            protector = default_character_sheet()
            protector["content"]["features"] = [
                {
                    "id": "dnd5e.content.srd2014.feature.fighter-fighting-style",
                    "name": "Fighting Style",
                    "choices": {"option": "Protection"},
                }
            ]
            protector, shield = add_inventory_item(
                protector,
                {
                    "id": "shield",
                    "name": "Shield",
                    "kind": "shield",
                    "mechanics": {"ac_bonus": 2},
                },
            )
            protector = equip_inventory_item(protector, shield, "shield")
            cid, rev, actors = await _campaign_with_combat(
                server,
                [("Caster", caster), ("Target", target), ("Protector", protector)],
                positions=[(0, 0), (3, 0), (3, 1)],
            )
            aid, tid, pid = [a["id"] for a in actors]
            armed = await ready(
                server,
                cid,
                rev,
                "ready_spell",
                {
                    "actor_id": aid,
                    "spell_id": spell["id"],
                    "trigger": "the bell rings",
                    "declaration": {"attacks": [{"target_id": tid} for _ in range(3)]},
                },
                "arm",
            )
            advanced = await _raw(
                server,
                "combat_end_turn",
                {
                    "campaign_id": cid,
                    "actor_id": aid,
                    "expected_revision": armed["campaign_revision"],
                    "idempotency_key": "end",
                },
            )
            triggered = await ready(
                server,
                cid,
                advanced["campaign_revision"],
                "trigger_spell",
                {
                    "readied_id": armed["readied"]["id"],
                    "event": "the bell rings",
                },
                "trigger",
            )
            before = await _campaign_actor_snapshot(server, cid, [aid, tid, pid])
            offered = await ready(
                server,
                cid,
                triggered["campaign_revision"],
                "resolve_spell",
                {
                    "actor_id": aid,
                    "choice_id": triggered["choice"]["id"],
                    "release": True,
                },
                "release",
            )
            assert offered["status"] == "pending_reaction"
            assert offered["released"]
            assert offered["result"]["attack_rolled"] is False
            assert offered["combat"]["readied"] == []
            window = next(
                w for w in offered["combat"]["pending"] if w.get("trigger") == "protection"
            )
            picked = await _call(
                server,
                "combat_choice",
                {
                    "campaign_id": cid,
                    "actor_id": pid,
                    "action": "resolve",
                    "payload": {"choice_id": window["id"], "selection": {"id": "protection"}},
                    "expected_revision": offered["campaign_revision"],
                    "idempotency_key": "protect",
                },
            )
            close_server(server)
            server = create_server(config)
            resume = offered["resume_attack"]
            assert resume["tool"] == "combat_resolve_attack"
            args = {
                **resume["arguments"],
                "expected_revision": picked["campaign_revision"],
                "idempotency_key": "resume",
            }
            result = await _raw(server, resume["tool"], args)
            assert len(result["result"]["rolls"]) == 2
            assert result["result"]["protection"]["actor_ids"] == [pid]
            assert result["result"]["spell_id"] == spell["id"]
            assert "protection_intent" not in result["combat"]
            after = await _campaign_actor_snapshot(server, cid, [aid, tid, pid])
            assert (
                after["actors"][0]["sheet"]["spellcasting"]["spell_slots"]
                == (before["actors"][0]["sheet"]["spellcasting"]["spell_slots"])
            )
            replay = await _raw(server, resume["tool"], args)
            assert replay == result
            assert await _campaign_actor_snapshot(server, cid, [aid, tid, pid]) == after
        finally:
            close_server(server)

    asyncio.run(run())
