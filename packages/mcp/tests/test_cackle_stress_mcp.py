from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.diseases import DISEASE_SOURCE_REF, infection_state

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server

DISEASE_SOURCE = "bundled:srd2014/08_Gamemastering/Diseases.md"
LESSER_RESTORATION_SOURCE = "bundled:srd2014/07_Spells/Spells_Each/Lesser_Restoration.md"


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _response(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


def _config(path: Path) -> McpConfig:
    return McpConfig(
        home=path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=path / "dnd",
        modulegen_skills_dir=path / "modulegen",
        auto_seed_rules=False,
    )


async def _cackle_actor(
    server,
    campaign_id: str,
    *,
    laughter_dc: int = 13,
    recovery_dc: int = 13,
    name: str = "Cackle target",
    idempotency_key: str = "cackle-actor",
    species: str = "human",
) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["species"] = species
    sheet["abilities"]["constitution"]["score"] = 1
    sheet["combat"]["hp"]["max"] = 100
    sheet["combat"]["hp"]["value"] = 100
    disease_state = infection_state(
        "cackle_fever",
        actor_id="pending",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    disease_state["actor_id"] = "pending"
    disease_state["laughter_dc"] = laughter_dc
    disease_state["recovery_dc"] = recovery_dc
    sheet["effects"].append(
        {
            "id": "test-cackle-disease",
            "name": "Cackle Fever",
            "kind": "disease_state",
            "source": DISEASE_SOURCE_REF,
            "active": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "metadata": {"disease_state": disease_state},
        }
    )
    sheet["effects"].append(
        {
            "id": "test-unrelated-incapacitation",
            "name": "Independent incapacitation",
            "kind": "timed_conditions",
            "source": "test:independent-condition",
            "active": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "incapacitated"}],
            "metadata": {},
        }
    )
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": campaign_id, "name": name, "sheet": sheet},
            "idempotency_key": idempotency_key,
        },
    )


async def _spread_target(
    server,
    campaign_id: str,
    *,
    name: str,
    idempotency_key: str,
    species: str = "human",
    con: int = 1,
) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["species"] = species
    sheet["abilities"]["constitution"]["score"] = con
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": campaign_id, "name": name, "sheet": sheet},
            "idempotency_key": idempotency_key,
        },
    )


async def _advance_to_symptoms(server, campaign_id: str, actor_id: str) -> str:
    effect_id = "test-cackle-disease"
    symptoms_due = 600
    campaign = await _call(
        server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign_id}}
    )
    elapsed = int(campaign["state"]["game_time"]["elapsed_ticks"])
    if symptoms_due > elapsed:
        await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign_id,
                "action": "clock_advance",
                "payload": {
                    "period": "hour",
                    "count": (symptoms_due - elapsed + 599) // 600,
                    "expected_elapsed_ticks": symptoms_due,
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "cackle-incubation-hour-1",
            },
        )
    return effect_id


async def _begin_single_actor_combat(server, campaign_id: str, actor_id: str) -> dict:
    campaign = await _call(
        server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign_id}}
    )
    return await _call(
        server,
        "combat_start",
        {
            "campaign_id": campaign_id,
            "positioning_mode": "grid",
            "battle_map": {"width_cells": 4, "height_cells": 4},
            "participant_ids": [actor_id],
            "participant_config": [
                {"actor_id": actor_id, "initiative": 20, "position": {"x": 1, "y": 1}}
            ],
            "expected_revision": campaign["revision"],
            "idempotency_key": "cackle-combat-start",
        },
    )


def test_cackle_stress_validates_source_and_replays_after_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle stress source and replay",
                    "edition": "2014",
                    "random_seed": "cackle-stress-source-replay",
                    "idempotency_key": "cackle-campaign",
                },
            )
            actor = await _cackle_actor(server, campaign["id"], laughter_dc=9)
            disease_effect_id = await _advance_to_symptoms(server, campaign["id"], actor["id"])
            started = await _begin_single_actor_combat(server, campaign["id"], actor["id"])
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            target = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            before_rng = current["state"].get("random_stream")
            invalid = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "trigger": "nightmare",
                "source_event_id": "not-a-logged-nightmare",
                "expected_revision": current["revision"],
                "expected_actor_revision": target["revision"],
                "idempotency_key": "reject-forged-nightmare",
            }
            try:
                await _response(server, "character_disease_stress", invalid)
            except Exception as error:
                assert "nightmare" in str(error).casefold()
            else:
                raise AssertionError("unlogged nightmare stress source was accepted")
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged["revision"] == current["revision"]
            assert unchanged["state"].get("random_stream") == before_rng

            request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "trigger": "entering_combat",
                "expected_revision": started["campaign_revision"],
                "expected_actor_revision": target["revision"],
                "idempotency_key": "cackle-enter-combat-stress",
            }
            response = await _response(server, "character_disease_stress", request)
            result = response["result"]
            assert result["disease_id"] == "cackle_fever"
            assert result["save"]["dc"] == 9
            assert response["random_stream_receipt"]
            assert any(
                "cackle_fever.stress.2014" in str(item) for item in response["rule_receipts"]
            )
            assert result["disease_state"]["resolved_stress_sources"]
            assert response["campaign_revision"] == started["campaign_revision"] + 1
            committed_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            committed_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert committed_campaign["state"]["random_stream"] != before_rng
            assert committed_actor["revision"] == target["revision"] + 1
            if result["events"][0]["kind"] == "psychic_damage":
                assert result["damage"]["damage_type"] == "psychic"
                assert result["damage_roll"]["total"] == result["events"][0]["rolled"]
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _response(server, "character_disease_stress", request) == response
            after_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_replay["revision"] == response["campaign_revision"]
            persisted = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            disease = next(
                item for item in persisted["sheet"]["effects"] if item["id"] == disease_effect_id
            )
            assert disease["metadata"]["disease_state"]["resolved_stress_sources"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_cackle_stress_failure_commits_save_damage_and_laughter_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle stress transaction",
                    "edition": "2014",
                    "random_seed": "cackle-stress-atomic-1",
                    "idempotency_key": "cackle-transaction-campaign",
                },
            )
            actor = await _cackle_actor(
                server,
                campaign["id"],
                idempotency_key="cackle-transaction-actor",
            )
            disease_effect_id = await _advance_to_symptoms(server, campaign["id"], actor["id"])
            started = await _begin_single_actor_combat(server, campaign["id"], actor["id"])
            before_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            before_hp = before_actor["sheet"]["combat"]["hp"]["value"]
            request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "trigger": "entering_combat",
                "expected_revision": started["campaign_revision"],
                "expected_actor_revision": before_actor["revision"],
                "idempotency_key": "cackle-one-transaction-stress",
            }
            original_replace = StateMutationService.replace

            def lose_stress_cas(self, campaign_id, **kwargs):
                if kwargs.get("operation") == "character.disease.stress":
                    kwargs["character_updates"] = [
                        replace(item, expected_revision=-1)
                        for item in kwargs.get("character_updates", [])
                    ]
                return original_replace(self, campaign_id, **kwargs)

            with monkeypatch.context() as patcher:
                patcher.setattr(StateMutationService, "replace", lose_stress_cas)
                with pytest.raises(Exception, match="revision conflict"):
                    await _response(server, "character_disease_stress", request)
            after_failed_cas_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            after_failed_cas_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert after_failed_cas_campaign["revision"] == before_campaign["revision"]
            assert after_failed_cas_campaign["state"] == before_campaign["state"]
            assert after_failed_cas_actor == before_actor

            response = await _response(server, "character_disease_stress", request)
            assert response["result"]["save"]["success"] is False
            assert response["result"]["damage_roll"]["expression"] == "1d10"
            assert response["result"]["damage"]["damage_type"] == "psychic"
            assert response["result"]["events"][0]["kind"] == "psychic_damage"
            assert response["result"]["events"][1]["kind"] == "mad_laughter_started"
            assert response["random_stream_receipt"]["draw_count"] == 2
            assert response["campaign_revision"] == before_campaign["revision"] + 1

            after_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            after_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert after_actor["revision"] == before_actor["revision"] + 1
            assert after_campaign["revision"] == before_campaign["revision"] + 1
            assert after_actor["sheet"]["combat"]["hp"]["value"] == (
                before_hp - response["result"]["damage"]["applied_amount"]
            )
            disease = next(
                item for item in after_actor["sheet"]["effects"] if item["id"] == disease_effect_id
            )
            laughter = next(
                item
                for item in after_actor["sheet"]["effects"]
                if item.get("metadata", {}).get("disease_condition_owner") == disease_effect_id
            )
            assert disease["metadata"]["disease_state"]["laughing"] is True
            assert laughter["active"] is True
            assert "incapacitated" in after_actor["sheet"]["conditions"]

            stale_request = {
                **request,
                "idempotency_key": "cackle-stale-stress-cas",
            }
            try:
                await _response(server, "character_disease_stress", stale_request)
            except Exception as error:
                assert "revision conflict" in str(error).casefold()
            else:
                raise AssertionError("stale Cackle stress CAS was accepted")
            after_stale_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            after_stale_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert after_stale_campaign["revision"] == after_campaign["revision"]
            assert (
                after_stale_campaign["state"]["random_stream"]
                == after_campaign["state"]["random_stream"]
            )
            assert after_stale_actor["revision"] == after_actor["revision"]
            assert after_stale_actor["sheet"] == after_actor["sheet"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_cackle_carrier_batch_is_sorted_atomic_and_replay_safe(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle carrier batch",
                    "edition": "2014",
                    "random_seed": "cackle-carrier-batch",
                    "idempotency_key": "cackle-batch-campaign",
                },
            )
            carrier = await _cackle_actor(
                server,
                campaign["id"],
                idempotency_key="cackle-batch-carrier",
            )
            high_save_targets = [
                await _spread_target(
                    server,
                    campaign["id"],
                    name=f"Likely save {index}",
                    idempotency_key=f"cackle-batch-save-target-{index}",
                    con=20,
                )
                for index in range(2)
            ]
            low_save_targets = [
                await _spread_target(
                    server,
                    campaign["id"],
                    name=f"Likely infection {index}",
                    idempotency_key=f"cackle-batch-infected-target-{index}",
                )
                for index in range(6)
            ]
            gnome = await _spread_target(
                server,
                campaign["id"],
                name="Immune gnome",
                idempotency_key="cackle-batch-gnome-target",
                species="gnome",
            )
            out_of_range = await _spread_target(
                server,
                campaign["id"],
                name="Out of range",
                idempotency_key="cackle-batch-distance-target",
            )
            targets = [*high_save_targets, *low_save_targets, gnome, out_of_range]
            await _advance_to_symptoms(server, campaign["id"], carrier["id"])
            started = await _begin_single_actor_combat(server, campaign["id"], carrier["id"])
            carrier_record = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": carrier["id"]}},
            )
            stress = await _response(
                server,
                "character_disease_stress",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": carrier["id"],
                    "trigger": "entering_combat",
                    "expected_revision": started["campaign_revision"],
                    "expected_actor_revision": carrier_record["revision"],
                    "idempotency_key": "cackle-batch-start-laughter",
                },
            )
            assert stress["result"]["save"]["success"] is False
            after_stress = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "outcome": {"status": "interrupted", "summary": "End Cackle setup combat."},
                    "expected_revision": after_stress["revision"],
                    "idempotency_key": "cackle-batch-end-combat",
                },
            )
            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            current_carrier = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": carrier["id"]}},
            )
            actor_records = [
                await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": item["id"]}},
                )
                for item in targets
            ]
            target_ids = [item["id"] for item in targets]
            revisions = {carrier["id"]: current_carrier["revision"]}
            revisions.update(
                {item["id"]: record["revision"] for item, record in zip(targets, actor_records)}
            )
            range_by_target = {item["id"]: item["id"] != targets[-1]["id"] for item in targets}
            request = {
                "campaign_id": campaign["id"],
                "carrier_actor_id": carrier["id"],
                "target_actor_ids": list(reversed(target_ids)),
                "exposure_source_ref": DISEASE_SOURCE,
                "target_turn_start_confirmed": True,
                "within_10_feet_by_target": range_by_target,
                "expected_revision": current_campaign["revision"],
                "expected_actor_revisions": revisions,
                "idempotency_key": "cackle-batch-settle",
            }
            stale = {
                **request,
                "expected_actor_revisions": {
                    **revisions,
                    max(target_ids): revisions[max(target_ids)] + 1,
                },
                "idempotency_key": "cackle-batch-stale-actor",
            }
            try:
                await _response(server, "character_disease_exposure_batch", stale)
            except Exception as error:
                assert "actor revision conflict" in str(error).casefold()
            else:
                raise AssertionError("stale target revision was accepted by Cackle spread batch")
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged["revision"] == current_campaign["revision"]
            assert unchanged["state"]["random_stream"] == current_campaign["state"]["random_stream"]
            for item, record in zip(targets, actor_records):
                after = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": item["id"]}},
                )
                assert after["revision"] == record["revision"]
                assert after["sheet"] == record["sheet"]

            before_cas_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_cas_actors = {
                item["id"]: await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": item["id"]}},
                )
                for item in [carrier, *targets]
            }
            original_replace = StateMutationService.replace

            def lose_cas(self, campaign_id, **kwargs):
                if kwargs.get("operation") == "character.disease.exposure.batch":
                    updates = list(kwargs.get("character_updates") or [])
                    target_update = next(
                        item for item in updates if item.character_id != carrier["id"]
                    )
                    kwargs["character_updates"] = [
                        replace(item, expected_revision=-1)
                        if item.character_id == target_update.character_id
                        else item
                        for item in updates
                    ]
                return original_replace(self, campaign_id, **kwargs)

            with monkeypatch.context() as patcher:
                patcher.setattr(StateMutationService, "replace", lose_cas)
                with pytest.raises(Exception, match="revision conflict"):
                    await _response(server, "character_disease_exposure_batch", request)
            after_cas_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_cas_campaign["revision"] == before_cas_campaign["revision"]
            assert after_cas_campaign["state"] == before_cas_campaign["state"]
            for actor_id, before_actor in before_cas_actors.items():
                after_actor = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor_id}},
                )
                assert after_actor == before_actor

            outcomes = await asyncio.gather(
                _response(server, "character_disease_exposure_batch", request),
                _response(
                    server,
                    "character_disease_exposure_batch",
                    {**request, "idempotency_key": "cackle-batch-concurrent-contender"},
                ),
                return_exceptions=True,
            )
            committed = [item for item in outcomes if isinstance(item, dict)]
            rejected = [item for item in outcomes if isinstance(item, Exception)]
            assert len(committed) == 1
            assert len(rejected) == 1
            response = committed[0]
            assert "revision conflict" in str(rejected[0]).casefold()
            result = response["result"]
            assert [item["target_actor_id"] for item in result["targets"]] == sorted(target_ids)
            by_target = {item["target_actor_id"]: item for item in result["targets"]}
            assert any(by_target[item["id"]]["status"] == "saved" for item in high_save_targets)
            assert any(by_target[item["id"]]["status"] == "infected" for item in low_save_targets)
            assert by_target[gnome["id"]]["status"] == "immune"
            assert by_target[out_of_range["id"]]["status"] == "out_of_range"
            assert by_target[gnome["id"]]["save"] is None
            assert by_target[out_of_range["id"]]["save"] is None
            assert response["random_stream_receipt"]["draw_count"] == sum(
                1 + int(item["save"]["success"] is False)
                for item in result["targets"]
                if item["save"] is not None
            )
            for item in result["targets"]:
                if item["status"] == "infected":
                    assert item["disease_state"]["incubation_roll"] in {1, 2, 3, 4}
                elif item["status"] == "saved":
                    immunity = item["carrier_immunity"]
                    assert immunity["target_actor_id"] == item["target_actor_id"]
                    assert immunity["carrier_actor_id"] == carrier["id"]

            batch_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "minute",
                        "count": 1,
                        "expected_elapsed_ticks": batch_campaign["state"]["game_time"][
                            "elapsed_ticks"
                        ]
                        + 10,
                    },
                    "expected_revision": batch_campaign["revision"],
                    "idempotency_key": "cackle-batch-later-marker",
                },
            )
            before_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            replay_request = {**request, "target_actor_ids": target_ids}
            replay = await _response(
                server,
                "character_disease_exposure_batch",
                replay_request,
            )
            assert replay == response
            after_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_replay["revision"] == before_replay["revision"]
            assert after_replay["state"]["random_stream"] == before_replay["state"]["random_stream"]
            changed_payload = {
                **replay_request,
                "within_10_feet_by_target": {
                    **range_by_target,
                    target_ids[0]: not range_by_target[target_ids[0]],
                },
            }
            with pytest.raises(Exception, match="idempotency key"):
                await _response(server, "character_disease_exposure_batch", changed_payload)
            after_changed_payload = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_changed_payload["revision"] == after_replay["revision"]
            assert after_changed_payload["state"] == after_replay["state"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_cackle_laughter_end_turn_requires_actor_and_cleans_only_owned_condition(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle end-turn recovery",
                    "edition": "2014",
                    "random_seed": "cackle-end-turn-recovery",
                    "idempotency_key": "cackle-campaign",
                },
            )
            actor = await _cackle_actor(server, campaign["id"], laughter_dc=11)
            disease_effect_id = await _advance_to_symptoms(server, campaign["id"], actor["id"])
            started = await _begin_single_actor_combat(server, campaign["id"], actor["id"])
            current_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "trigger": "entering_combat",
                "expected_revision": started["campaign_revision"],
                "expected_actor_revision": current_actor["revision"],
                "idempotency_key": "cackle-start-laughter",
            }
            stress = await _response(server, "character_disease_stress", request)
            assert stress["result"]["events"][0]["kind"] == "psychic_damage"
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            laughing_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            disease = next(
                item
                for item in laughing_actor["sheet"]["effects"]
                if item["id"] == disease_effect_id
            )
            sidecar = next(
                item
                for item in laughing_actor["sheet"]["effects"]
                if item.get("metadata", {}).get("disease_condition_owner") == disease_effect_id
            )
            assert sidecar["active"] is True
            assert disease["active"] is True
            assert "incapacitated" in sidecar["changes"][0]["value"]

            wrong_actor_request = {
                "campaign_id": campaign["id"],
                "actor_id": "not-the-active-turn-actor",
                "expected_revision": current["revision"],
                "expected_actor_revision": 0,
                "idempotency_key": "reject-non-active-laugher",
            }
            try:
                await _response(server, "character_disease_end_turn", wrong_actor_request)
            except Exception as error:
                assert "active turn" in str(error).casefold()
            else:
                raise AssertionError("end-turn recovery was accepted for a non-active actor")

            failed_request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "expected_revision": current["revision"],
                "expected_actor_revision": laughing_actor["revision"],
                "idempotency_key": "cackle-end-turn-failed-save",
            }
            failed = await _response(server, "character_disease_end_turn", failed_request)
            assert failed["result"]["save"]["dc"] == 11
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _response(server, "character_disease_end_turn", failed_request) == failed
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            sidecar_after = next(
                item for item in after["sheet"]["effects"] if item["id"] == sidecar["id"]
            )
            assert sidecar_after["active"] is True

            saved = False
            current_result = failed
            for attempt in range(1, 101):
                current = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                current_actor = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                next_request = {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "expected_revision": current["revision"],
                    "expected_actor_revision": current_actor["revision"],
                    "idempotency_key": f"cackle-end-turn-repeat-{attempt}",
                }
                current_result = await _response(server, "character_disease_end_turn", next_request)
                if current_result["result"]["save"]["success"] is True:
                    saved = True
                    break
            assert saved, (
                "seeded campaign did not produce a successful end-turn save within 100 tries"
            )
            cleaned = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            owned_sidecar = next(
                item for item in cleaned["sheet"]["effects"] if item["id"] == sidecar["id"]
            )
            assert owned_sidecar["active"] is False
            disease_after = next(
                item for item in cleaned["sheet"]["effects"] if item["id"] == disease_effect_id
            )
            assert disease_after["active"] is True
            assert disease_after["metadata"]["disease_state"]["laughing"] is False
            unrelated_sidecar = next(
                item
                for item in cleaned["sheet"]["effects"]
                if item["id"] == "test-unrelated-incapacitation"
            )
            assert unrelated_sidecar["active"] is True
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_cackle_cure_during_laughter_cleans_only_owned_incapacitation(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle cure rider ownership",
                    "edition": "2014",
                    "random_seed": "cackle-end-turn-recovery",
                    "idempotency_key": "cackle-cure-campaign",
                },
            )
            actor = await _cackle_actor(server, campaign["id"])
            await _advance_to_symptoms(server, campaign["id"], actor["id"])
            started = await _begin_single_actor_combat(server, campaign["id"], actor["id"])
            target = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            stressed = await _response(
                server,
                "character_disease_stress",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "trigger": "entering_combat",
                    "expected_revision": started["campaign_revision"],
                    "expected_actor_revision": target["revision"],
                    "idempotency_key": "cackle-cure-enter-combat",
                },
            )
            assert stressed["result"]["events"][0]["kind"] == "psychic_damage"
            laughing = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            disease = next(
                item for item in laughing["sheet"]["effects"] if item["id"] == "test-cackle-disease"
            )
            laughter = next(
                item
                for item in laughing["sheet"]["effects"]
                if item.get("metadata", {}).get("disease_condition_owner") == disease["id"]
            )
            assert laughter["active"] is True

            campaign_now = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            cured = await _response(
                server,
                "character_disease_cure",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "disease_effect_id": disease["id"],
                    "cure_source_id": "lesser_restoration",
                    "cure_source_ref": LESSER_RESTORATION_SOURCE,
                    "expected_revision": campaign_now["revision"],
                    "expected_actor_revision": laughing["revision"],
                    "idempotency_key": "cackle-cure-laughter",
                },
            )
            assert cured["result"]["disease_id"] == "cackle_fever"
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            cured_disease = next(
                item for item in after["sheet"]["effects"] if item["id"] == disease["id"]
            )
            cured_laughter = next(
                item for item in after["sheet"]["effects"] if item["id"] == laughter["id"]
            )
            unrelated = next(
                item
                for item in after["sheet"]["effects"]
                if item["id"] == "test-unrelated-incapacitation"
            )
            assert cured_disease["active"] is False
            assert cured_disease["metadata"]["disease_state"]["laughing"] is False
            assert cured_laughter["active"] is False
            assert unrelated["active"] is True
            assert "incapacitated" in after["sheet"]["conditions"]
        finally:
            close_server(server)

    asyncio.run(exercise())
