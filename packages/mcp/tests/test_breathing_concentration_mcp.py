import asyncio
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.standard_spell_ids import CORE_FLY_SPELL_ID, CORE_INVISIBILITY_SPELL_ID
from test_structured_spell_mcp import _call, _config, _fly, _invisibility

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize(
    ("spell_id", "spell_factory", "slot_level", "target_kind", "target_condition"),
    (
        (CORE_FLY_SPELL_ID, _fly, 3, "spell_fly", None),
        (CORE_INVISIBILITY_SPELL_ID, _invisibility, 2, "spell_invisibility", "invisible"),
    ),
)
def test_breathing_expiry_reconciles_real_concentration_target(
    tmp_path: Path,
    spell_id: str,
    spell_factory,
    slot_level: int,
    target_kind: str,
    target_condition: str | None,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Breathing concentration", "edition": "2014", "idempotency_key": "campaign"},
        )
        caster_sheet = default_character_sheet()
        caster_sheet["spellcasting"].update(
            ability="intelligence",
            spell_slots={
                str(slot_level): {
                    "label": f"{slot_level}th",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "long_rest",
                    "source_key": "wizard",
                }
            },
        )
        caster_sheet["content"]["spells"] = [spell_factory()]
        caster = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Caster", "sheet": caster_sheet},
                "idempotency_key": "caster",
            },
        )
        target = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Target",
                    "sheet": default_character_sheet(),
                },
                "idempotency_key": "target",
            },
        )
        cast = await _call(
            server,
            "character_action",
            {
                "character_id": caster["id"],
                "action": "cast_spell",
                "payload": {
                    "spell_id": spell_id,
                    "cast_level": slot_level,
                    "target_character_ids": [target["id"]],
                    **(
                        {"willing_target_ids": [target["id"]]}
                        if spell_id == CORE_FLY_SPELL_ID
                        else {}
                    ),
                },
                "expected_revision": caster["revision"],
                "idempotency_key": "cast",
            },
        )
        assert cast["result"]["automatic_effect"] == target_kind.removeprefix("spell_")
        assert cast["result"]["concentration_effect_id"]
        assert cast["result"]["effect_ids"]
        target_before = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": target["id"]}}
        )
        target_effect_before = next(
            item
            for item in target_before["sheet"]["effects"]
            if item["id"] == cast["result"]["effect_ids"][target["id"]]
        )
        assert target_effect_before["active"] is True
        if target_condition:
            assert target_condition in target_before["sheet"]["conditions"]
        caster_after = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": caster["id"]}}
        )
        choking = await _call(
            server,
            "character_state_change",
            {
                "character_id": caster["id"],
                "action": "breathing_transition",
                "payload": {"can_breathe": False, "choking": True},
                "expected_revision": caster_after["revision"],
                "idempotency_key": "choke",
            },
        )
        assert (
            await _call(
                server,
                "character_state_change",
                {
                    "character_id": caster["id"],
                    "action": "breathing_transition",
                    "payload": {"can_breathe": False, "choking": True},
                    "expected_revision": caster_after["revision"],
                    "idempotency_key": "choke",
                },
            )
            == choking
        )
        clock = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        expired = await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {"period": "round", "count": 1},
                "expected_revision": clock["revision"],
                "idempotency_key": "expire",
            },
        )
        target_after_expiry = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": target["id"]}}
        )
        replayed_expiry = await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {"period": "round", "count": 1},
                "expected_revision": clock["revision"],
                "idempotency_key": "expire",
            },
        )
        assert replayed_expiry == expired
        target_after_replay = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": target["id"]}}
        )
        assert target_after_replay["revision"] == target_after_expiry["revision"]
        close_server(server)
        restarted = create_server(config)
        caster_final = await _call(
            restarted, "character_query", {"view": "get", "payload": {"character_id": caster["id"]}}
        )
        target_final = await _call(
            restarted, "character_query", {"view": "get", "payload": {"character_id": target["id"]}}
        )
        assert caster_final["sheet"]["combat"]["hp"]["value"] == 0
        assert any(
            item["active"] is False and item.get("ended_reason") == "incapacitated"
            for item in caster_final["sheet"]["effects"]
            if item.get("concentration")
        )
        target_effect = next(
            item
            for item in target_final["sheet"]["effects"]
            if item["id"] == target_effect_before["id"]
        )
        assert target_effect["active"] is False
        assert target_effect["ended_reason"] == "source_effect_ended"
        if target_condition:
            assert target_condition not in target_final["sheet"]["conditions"]
        close_server(restarted)

    asyncio.run(exercise())
