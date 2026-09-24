from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import Database, RulePackService
from sagasmith_core.database import sqlite_database_url
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.content_validation import build_catalog_review

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server

DISEASE_SOURCE = "bundled:srd2014/08_Gamemastering/Diseases.md"
LESSER_RESTORATION_SOURCE = "bundled:srd2014/07_Spells/Spells_Each/Lesser_Restoration.md"


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _call_response(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


def _config(path: Path, rule_root: Path | None = None) -> McpConfig:
    return McpConfig(
        home=path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=path / "dnd",
        modulegen_skills_dir=path / "modulegen",
        auto_seed_rules=False,
        rule_import_roots=(rule_root,) if rule_root is not None else (),
    )


async def _actor(server, campaign_id: str) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["species"] = "human"
    sheet["abilities"]["constitution"]["score"] = 1
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": campaign_id, "name": "Disease target", "sheet": sheet},
            "idempotency_key": "disease-target",
        },
    )


def test_disease_exposure_is_campaign_random_cas_persisted_and_cure_is_source_bound(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Disease lifecycle",
                    "edition": "2014",
                    "random_seed": "disease-exposure-restart",
                    "idempotency_key": "disease-campaign",
                },
            )
            actor = await _actor(server, campaign["id"])
            result = None
            request = None
            for attempt in range(20):
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
                request = {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "disease_id": "sight_rot",
                    "exposure_kind": "tainted_water",
                    "exposure_source_id": "scene:tainted-water-1",
                    "exposure_source_ref": DISEASE_SOURCE,
                    "expected_revision": current["revision"],
                    "expected_actor_revision": target["revision"],
                    "idempotency_key": f"disease-water-sip-{attempt}",
                }
                response = await _call_response(server, "character_disease_exposure", request)
                result = response["result"]
                if result["status"] == "infected":
                    break
                assert result["status"] == "saved"
            else:
                raise AssertionError("seeded campaign did not produce a failed Sight Rot save")

            infected = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            effect = next(
                item
                for item in infected["sheet"]["effects"]
                if item["id"] == result["disease_effect"]["id"]
            )
            disease_state = effect["metadata"]["disease_state"]
            assert effect["active"] is True
            assert disease_state["source_ref"] == DISEASE_SOURCE
            assert disease_state.get("incubation_roll") is None
            assert disease_state["symptoms_due_elapsed_ticks"] == 14400
            assert result["save"]["dc"] == 15
            assert response["rule_receipts"]

            replacement = deepcopy(infected["sheet"])
            disease_effect_id = result["disease_effect"]["id"]
            replacement["effects"] = [
                item for item in replacement["effects"] if item["id"] != disease_effect_id
            ]
            with pytest.raises(ToolError, match="source-owned disease lifecycle"):
                await _call(
                    server,
                    "character_sheet_replace",
                    {
                        "character_id": actor["id"],
                        "sheet": replacement,
                        "expected_revision": infected["revision"],
                        "idempotency_key": "cannot-replace-disease-state",
                    },
                )

            with pytest.raises(ToolError, match="source-owned disease effects"):
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "effect_remove",
                        "payload": {"effect_id": result["disease_effect"]["id"]},
                        "expected_revision": infected["revision"],
                        "idempotency_key": "cannot-remove-disease-effect",
                    },
                )
            with pytest.raises(ToolError, match="source-owned disease effects"):
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "effect_add",
                        "payload": {
                            "effect": {
                                "id": "forged-disease",
                                "name": "Forged disease",
                                "kind": "disease_state",
                                "active": True,
                                "source": "caller:fake",
                            }
                        },
                        "expected_revision": infected["revision"],
                        "idempotency_key": "cannot-add-disease-effect",
                    },
                )

            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call_response(server, "character_disease_exposure", request) == response
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            clocked = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {"period": "hour", "count": 16, "expected_elapsed_ticks": 9600},
                    "expected_revision": current["revision"],
                    "idempotency_key": "disease-sight-rot-incubation-day",
                },
            )
            current_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            disease_effect = next(
                item
                for item in current_actor["sheet"]["effects"]
                if item["id"] == effect["id"]
            )
            assert disease_effect["metadata"]["disease_state"]["symptomatic"] is False
            assert disease_effect["changes"] == []
            assert disease_effect["metadata"]["attack_roll_penalty"] == 0

            rest_request = {
                "campaign_id": campaign["id"],
                "action": "party_rest",
                "payload": {
                    "rest_type": "long_rest",
                    "duration_minutes": 480,
                    "members": [
                        {
                            "character_id": actor["id"],
                            "expected_revision": current_actor["revision"],
                            "survival_intake": {
                                "food_lb": 1,
                                "water_gallons": 1,
                                "hot_weather": False,
                            },
                        }
                    ],
                },
                "expected_revision": clocked["campaign_revision"],
                "idempotency_key": "disease-sight-rot-long-rest-worsening",
            }
            rested = await _call(server, "campaign_change", rest_request)
            disease_rest = rested.get("recovered", {}).get(actor["id"], {}).get("disease_rest", [])
            assert disease_rest and disease_rest[0]["disease_id"] == "sight_rot", (
                rested.get("reason"), rested.get("missing"), rested.get("committed")
            )
            post_rest_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            disease_effect = next(
                item
                for item in post_rest_actor["sheet"]["effects"]
                if item["id"] == effect["id"]
            )
            assert disease_effect["metadata"]["disease_state"]["symptomatic"] is True
            assert disease_effect["metadata"]["disease_state"]["sight_penalty"] == 1
            assert disease_effect["changes"] == [
                {"path": "rolls.attack.bonus", "mode": "add", "value": -1}
            ]

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "sight-rot-check-phase",
                },
            )

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_unreviewed = current
            with pytest.raises(ToolError, match="structured check_context"):
                await _call(
                    server,
                    "character_check",
                    {
                        "campaign_id": campaign["id"],
                        "action": "check",
                        "payload": {
                            "actor_id": actor["id"],
                            "kind": "ability",
                            "ability": "perception",
                            "dc": 1,
                            "relies_on_sight": True,
                        },
                        "expected_revision": current["revision"],
                        "idempotency_key": "sight-rot-forged-bool",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged["revision"] == before_unreviewed["revision"]
            assert unchanged["state"]["random_stream"]["position"] == before_unreviewed[
                "state"
            ]["random_stream"]["position"]

            visual_check = await _call_response(
                server,
                "character_check",
                {
                    "campaign_id": campaign["id"],
                    "action": "check",
                    "payload": {
                        "actor_id": actor["id"],
                        "kind": "ability",
                        "ability": "perception",
                        "dc": 1,
                        "check_context": {
                            "task": "Read the visual markings on the water vial.",
                            "sensory_basis": "sight",
                            "reason": "The resolved task requires reading visible markings.",
                        },
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "sight-rot-visual-ability-check",
                },
            )
            assert visual_check["result"]["total"] - visual_check["result"]["natural"] == -1
            assert visual_check["rule_receipts"][-1]["mechanic_id"] == (
                "dnd5e.core.gamemastering.disease.sight_rot.sight_dependent_checks.2014"
            )
            assert visual_check["rule_receipts"][-1]["facts"]["disease_effect_id"] == effect["id"]

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            nonvisual_check = await _call_response(
                server,
                "character_check",
                {
                    "campaign_id": campaign["id"],
                    "action": "check",
                    "payload": {
                        "actor_id": actor["id"],
                        "kind": "ability",
                        "ability": "perception",
                        "dc": 1,
                        "check_context": {
                            "task": "Identify the container by touch and smell.",
                            "sensory_basis": "nonvisual",
                            "reason": "This check explicitly uses nonvisual cues.",
                        },
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "sight-rot-nonvisual-ability-check",
                },
            )
            assert nonvisual_check["result"]["total"] - nonvisual_check["result"]["natural"] == 0
            assert "rule_receipts" not in nonvisual_check or not any(
                receipt.get("mechanic_id")
                == "dnd5e.core.gamemastering.disease.sight_rot.sight_dependent_checks.2014"
                for receipt in nonvisual_check["rule_receipts"]
            )

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            infected = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            cure_request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "disease_effect_id": effect["id"],
                "cure_source_id": "lesser_restoration",
                "cure_source_ref": LESSER_RESTORATION_SOURCE,
                "expected_revision": current["revision"],
                "expected_actor_revision": infected["revision"],
                "idempotency_key": "cure-sight-rot",
            }
            cured = await _call_response(server, "character_disease_cure", cure_request)
            assert cured["status"] == "committed"
            assert await _call(server, "campaign_change", rest_request) == rested
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call_response(server, "character_disease_cure", cure_request) == cured
            cured_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            cured_effect = next(
                item for item in cured_actor["sheet"]["effects"] if item["id"] == effect["id"]
            )
            assert cured_effect["active"] is False
            assert cured_effect["metadata"]["disease_state"]["active"] is False
            assert cured_effect["metadata"]["disease_state"]["symptomatic"] is True
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_disease_exposure_rejects_unreviewed_source_before_campaign_or_actor_change(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Disease source guard", "edition": "2014", "idempotency_key": "c"},
            )
            actor = await _actor(server, campaign["id"])
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_stream = current["state"].get("random_stream")
            try:
                await _call(
                    server,
                    "character_disease_exposure",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "disease_id": "sight_rot",
                        "exposure_kind": "tainted_water",
                        "exposure_source_id": "scene:unknown-water",
                        "exposure_source_ref": "caller:made-up-rules",
                        "expected_revision": current["revision"],
                        "expected_actor_revision": actor["revision"],
                        "idempotency_key": "reject-unreviewed-disease-source",
                    },
                )
            except Exception as error:
                assert "source" in str(error).casefold()
            else:
                raise AssertionError("unreviewed disease source was accepted")
            after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after["revision"] == current["revision"]
            assert after["state"].get("random_stream") == before_stream
        finally:
            close_server(server)

    asyncio.run(exercise())


def _reviewed_variant_artifact(
    pack_id: str,
    disease_id: str,
    values: dict,
) -> dict:
    artifact = {
        "id": f"{pack_id}.{disease_id}",
        "kind": "disease_variant",
        "card": {"name": f"Campaign {disease_id}"},
        "source_citations": [
            {
                "source": "campaign-source:disease-appendix",
                "source_ref": {"section": disease_id},
                "source_excerpt": "Campaign-reviewed 2014 disease variant.",
            }
        ],
        "disease_variant": {
            "schema_version": 1,
            "edition": "2014",
            "disease_id": disease_id,
            **values,
        },
    }
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            {
                "role": "dm",
                "reviewer": "campaign-dm",
                "method": "human",
                "checks": {
                    "identity": True,
                    "classification": True,
                    "entry_boundary": True,
                    "references": True,
                },
                "notes": "Reviewed against the campaign disease appendix.",
            }
        ],
        status="approved",
    )
    return artifact


def _seed_disease_variant_pack(
    config: McpConfig,
    pack_id: str,
    artifacts: list[dict],
    *,
    install: bool = True,
) -> dict:
    database = Database(sqlite_database_url(config.database_path))
    try:
        packs = RulePackService(database)
        draft = packs.save_draft(
            manifest={
                "id": pack_id,
                "version": "1.0.0",
                "title": "Campaign disease variants",
                "namespace": pack_id,
                "system_id": "dnd5e",
                "editions": ["2014"],
            },
            artifacts=artifacts,
            provenance={"source": "campaign-reviewed disease appendix"},
        )
        assert draft.status == "validated"
        if install:
            version = packs.install(pack_id, "1.0.0")
            assert version.status == "installed"
        else:
            version = draft
        return {"checksum": version.checksum, "version": version.version, "status": version.status}
    finally:
        database.dispose()


async def _activate_disease_variant_pack(server, campaign_id: str, pack_id: str, key: str) -> dict:
    current = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    activation = await _call(
        server,
        "content_pack",
        {
            "action": "activate",
            "payload": {
                "kind": "core_rules",
                "campaign_id": campaign_id,
                "pack_id": pack_id,
                "version": "1.0.0",
                "enabled": True,
            },
            "principal_id": "system:local",
            "expected_revision": current["revision"],
            "idempotency_key": key,
        },
    )
    installed = await _call(
        server,
        "content_pack",
        {
            "action": "get",
            "payload": {
                "kind": "core_rules",
                "campaign_id": campaign_id,
                "pack_id": pack_id,
                "version": "1.0.0",
            },
            "principal_id": "system:local",
        },
    )
    exact_lock = next(
        item for item in activation["effective"]["lock"] if item["pack_id"] == pack_id
    )
    assert exact_lock["checksum"] == installed["checksum"]
    assert activation["activation"]["branch_id"] == activation["effective"]["branch_id"]
    return activation


async def _expose_disease(server, campaign_id: str, actor_id: str, disease_id: str, key: str):
    campaign = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    actor = await _call(
        server,
        "character_query",
        {"view": "get", "payload": {"character_id": actor_id}},
    )
    exposure_kind = "contaminated_filth" if disease_id == "sewer_plague" else "tainted_water"
    request = {
        "campaign_id": campaign_id,
        "actor_id": actor_id,
        "disease_id": disease_id,
        "exposure_kind": exposure_kind,
        "exposure_source_id": f"scene:{key}",
        "exposure_source_ref": DISEASE_SOURCE,
        "expected_revision": campaign["revision"],
        "expected_actor_revision": actor["revision"],
        "idempotency_key": key,
    }
    response = await _call_response(server, "character_disease_exposure", request)
    return request, response


def test_reviewed_disease_variant_requires_active_exact_pack_and_pins_public_resolution(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        pack_id = "campaign.disease_variants"
        sewer = _reviewed_variant_artifact(
            pack_id,
            "sewer_plague",
            {
                "save_dcs": {"infection": 30, "recovery": 12},
                "incubation": {"fixed": 2, "unit": "day"},
                "eligible_creature_types": ["humanoid"],
            },
        )
        sight = _reviewed_variant_artifact(
            pack_id,
            "sight_rot",
            {
                "save_dcs": {"infection": 20},
                "incubation": {"fixed": 3, "unit": "day"},
                "eligible_creature_types": ["beast"],
            },
        )
        stale_pack_id = "campaign.stale_disease_variants"
        stale = _reviewed_variant_artifact(
            stale_pack_id,
            "sewer_plague",
            {
                "save_dcs": {"infection": 29},
                "incubation": {"fixed": 4, "unit": "day"},
            },
        )
        stale["disease_variant"]["save_dcs"]["infection"] = 28
        uninstalled_pack_id = "campaign.uninstalled_disease_variants"
        uninstalled_variant = _reviewed_variant_artifact(
            uninstalled_pack_id,
            "sight_rot",
            {
                "save_dcs": {"infection": 19},
                "eligible_creature_types": ["beast"],
            },
        )
        server = create_server(config)
        try:
            installed = _seed_disease_variant_pack(config, pack_id, [sewer, sight])
            _seed_disease_variant_pack(config, stale_pack_id, [stale])
            uninstalled = _seed_disease_variant_pack(
                config,
                uninstalled_pack_id,
                [uninstalled_variant],
                install=False,
            )
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Reviewed disease variants",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            inactive_actor = await _actor(server, campaign["id"])
            active_actor = await _actor(server, campaign["id"])
            gate_actor = await _actor(server, campaign["id"])

            before_failed_activation = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            stale_activation = {
                "action": "activate",
                "payload": {
                    "kind": "core_rules",
                    "campaign_id": campaign["id"],
                    "pack_id": pack_id,
                    "version": "1.0.0",
                    "enabled": True,
                },
                "principal_id": "system:local",
                "expected_revision": before_failed_activation["revision"] - 1,
                "idempotency_key": "reject-stale-variant-activation",
            }
            with pytest.raises(ToolError, match="campaign revision conflict"):
                await _call(server, "content_pack", stale_activation)
            after_stale_activation = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_stale_activation["revision"] == before_failed_activation["revision"]
            assert after_stale_activation["state"] == before_failed_activation["state"]

            with pytest.raises(ToolError, match="installed"):
                await _call(
                    server,
                    "content_pack",
                    {
                        **stale_activation,
                        "payload": {
                            **stale_activation["payload"],
                            "pack_id": uninstalled_pack_id,
                        },
                        "expected_revision": after_stale_activation["revision"],
                        "idempotency_key": "reject-uninstalled-variant-activation",
                    },
                )
            after_uninstalled_activation = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_uninstalled_activation["revision"] == after_stale_activation["revision"]
            assert after_uninstalled_activation["state"] == after_stale_activation["state"]
            assert uninstalled["status"] == "validated"

            _, inactive_response = await _expose_disease(
                server, campaign["id"], inactive_actor["id"], "sewer_plague", "variant-inactive"
            )
            assert inactive_response["result"]["save"]["dc"] == 11
            assert not any(
                receipt.get("facts", {}).get("disease_variant")
                for receipt in inactive_response["rule_receipts"]
            )

            await _activate_disease_variant_pack(
                server,
                campaign["id"],
                pack_id,
                "activate-disease-variants",
            )
            before_stale_actor_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_stale_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": active_actor["id"]}},
            )
            with pytest.raises(ToolError, match="actor revision conflict"):
                await _call(
                    server,
                    "character_disease_exposure",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": active_actor["id"],
                        "disease_id": "sewer_plague",
                        "exposure_kind": "contaminated_filth",
                        "exposure_source_id": "scene:variant-stale-actor",
                        "exposure_source_ref": DISEASE_SOURCE,
                        "expected_revision": before_stale_actor_campaign["revision"],
                        "expected_actor_revision": before_stale_actor["revision"] + 1,
                        "idempotency_key": "reject-stale-variant-actor",
                    },
                )
            after_stale_actor_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            after_stale_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": active_actor["id"]}},
            )
            assert after_stale_actor_campaign["revision"] == before_stale_actor_campaign["revision"]
            assert after_stale_actor_campaign["state"] == before_stale_actor_campaign["state"]
            assert after_stale_actor == before_stale_actor
            request, response = await _expose_disease(
                server, campaign["id"], active_actor["id"], "sewer_plague", "variant-sewer"
            )
            result = response["result"]
            assert result["status"] == "infected"
            assert result["save"]["dc"] == 30
            state = result["disease_state"]
            assert state["symptoms_due_elapsed_ticks"] == 28_800
            receipt = state["variant_receipt"]
            assert receipt["pack_id"] == pack_id
            assert receipt["pack_checksum"] == installed["checksum"]
            assert receipt["artifact_id"] == f"{pack_id}.sewer_plague"
            assert any(
                item.get("facts", {}).get("disease_variant") == receipt
                for item in response["rule_receipts"]
            )

            before_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            actor_before_replay = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": active_actor["id"]}},
            )
            assert await _call_response(server, "character_disease_exposure", request) == response
            after_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            actor_after_replay = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": active_actor["id"]}},
            )
            assert after_replay["revision"] == before_replay["revision"]
            assert after_replay["state"]["random_stream"] == before_replay["state"]["random_stream"]
            assert actor_after_replay == actor_before_replay

            gate_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            gate_actor_before = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": gate_actor["id"]}},
            )
            _, gate_response = await _expose_disease(
                server, campaign["id"], gate_actor["id"], "sight_rot", "variant-creature-gate"
            )
            assert gate_response["result"]["status"] == "ineligible"
            assert gate_response["result"]["save"] is None
            assert any(
                item.get("facts", {}).get("disease_variant", {}).get("artifact_id")
                == f"{pack_id}.sight_rot"
                for item in gate_response["rule_receipts"]
            )
            gate_actor_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": gate_actor["id"]}},
            )
            assert gate_actor_after == gate_actor_before
            gate_campaign_after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert (
                gate_campaign_after["state"]["random_stream"]
                == gate_campaign["state"]["random_stream"]
            )

            stale_campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Stale disease review",
                    "edition": "2014",
                    "idempotency_key": "stale-campaign",
                },
            )
            stale_actor = await _actor(server, stale_campaign["id"])
            await _activate_disease_variant_pack(
                server, stale_campaign["id"], stale_pack_id, "activate-stale-disease-variant"
            )
            before_stale = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": stale_campaign["id"]}},
            )
            stale_actor_before = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": stale_actor["id"]}},
            )
            with pytest.raises(ToolError, match="review is stale"):
                await _expose_disease(
                    server,
                    stale_campaign["id"],
                    stale_actor["id"],
                    "sewer_plague",
                    "reject-stale-disease-review",
                )
            after_stale = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": stale_campaign["id"]}},
            )
            stale_actor_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": stale_actor["id"]}},
            )
            assert after_stale["revision"] == before_stale["revision"]
            assert after_stale["state"] == before_stale["state"]
            assert stale_actor_after == stale_actor_before
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_disease_variant_import_compiles_server_review_and_activates_publicly(
    tmp_path: Path,
) -> None:
    import_root = tmp_path / "imports"
    import_root.mkdir()
    source = import_root / "disease-appendix.md"
    source.write_text(
        "# Sewer Plague\n\n"
        "Sewer Plague is a 2014 disease variant. Infection requires a DC 30 Constitution "
        "saving throw. Its incubation period is 2 days. It infects humanoids.\n",
        encoding="utf-8",
    )

    async def exercise() -> None:
        config = _config(tmp_path, import_root)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Imported disease variant",
                    "edition": "2014",
                    "idempotency_key": "disease-variant-campaign",
                },
            )
            started = await _call(
                server,
                "rulebook_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "source_path": str(source),
                        "source_key": "reviewed-disease-source",
                        "title": "Reviewed Disease Source",
                        "edition": "2014",
                    },
                    "idempotency_key": "disease-import-start",
                },
            )
            job = started["job"]
            chunks = await _call(
                server,
                "rulebook_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "evidence",
                    "payload": {
                        "job_id": job["id"],
                        "kind": "chunks",
                        "query": "Sewer Plague",
                    },
                },
            )
            source_chunk_id = next(
                item["id"] for item in chunks if "Sewer Plague" in item.get("content", "")
            )
            augmented = await _call(
                server,
                "rulebook_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "edit",
                    "payload": {
                        "job_id": job["id"],
                        "operation": "catalog",
                        "rationale": (
                            "Map this source-backed disease variant to the bounded runtime "
                            "schema."
                        ),
                        "additions": [
                            {
                                "kind": "disease_variant",
                                "name": "Sewer Plague",
                                "source_chunk_ids": [source_chunk_id],
                                "card": {},
                                "disease_variant": {
                                    "schema_version": 1,
                                    "edition": "2014",
                                    "disease_id": "sewer_plague",
                                    "save_dcs": {"infection": 30},
                                    "incubation": {"fixed": 2, "unit": "day"},
                                    "eligible_creature_types": ["humanoid"],
                                },
                            }
                        ],
                    },
                    "expected_revision": job["revision"],
                    "idempotency_key": "disease-import-augment",
                },
            )
            candidate_id = augmented["added_candidate_ids"][0]
            candidate = next(
                item for item in augmented["candidates"] if item["id"] == candidate_id
            )
            forged_artifact = deepcopy(candidate["artifact"])
            forged_artifact["catalog_review"] = {
                "schema_version": 1,
                "status": "approved",
                "reviewed_content_hash": "0" * 64,
                "decisions": [
                    {
                        "role": "dm",
                        "reviewer": "caller:forged-review",
                        "method": "human",
                        "checks": {
                            "identity": True,
                            "classification": True,
                            "entry_boundary": True,
                            "references": True,
                        },
                        "notes": "Caller supplied review metadata must not be trusted.",
                    }
                ],
            }
            edited = await _call(
                server,
                "rulebook_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "edit",
                    "payload": {
                        "job_id": job["id"],
                        "operation": "candidates",
                        "decisions": [
                            {
                                "id": candidate_id,
                                "review_status": "accepted",
                                "artifact": forged_artifact,
                                "note": "Reviewed the typed variant against the indexed source.",
                            }
                        ],
                    },
                    "expected_revision": augmented["job"]["revision"],
                    "idempotency_key": "disease-import-review",
                },
            )
            finalized = await _call(
                server,
                "rulebook_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "finalize",
                    "payload": {
                        "job_id": job["id"],
                        "confirmation": {
                            "confirmed": True,
                            "note": (
                                "Freeze the source-backed Sewer Plague variant for "
                                "compilation."
                            ),
                        },
                        "manifest": {
                            "id": "campaign.imported_disease",
                            "version": "1.0.0",
                            "title": "Imported Disease Rules",
                            "namespace": "campaign.imported_disease",
                            "system_id": "dnd5e",
                            "editions": ["2014"],
                        },
                        "include_package": True,
                    },
                    "expected_revision": edited["job"]["revision"],
                    "idempotency_key": "disease-import-finalize",
                },
            )
            assert finalized["draft"]["status"] == "validated"
            assert finalized["stored"]["status"] == "stored"
            activation = await _activate_disease_variant_pack(
                server,
                campaign["id"],
                "campaign.imported_disease",
                "activate-imported-disease",
            )
            assert activation["activation"]["branch_id"] == activation["effective"]["branch_id"]
            installed = await _call(
                server,
                "content_pack",
                {
                    "action": "get",
                    "payload": {
                        "kind": "core_rules",
                        "campaign_id": campaign["id"],
                        "pack_id": "campaign.imported_disease",
                        "version": "1.0.0",
                    },
                    "principal_id": "system:local",
                },
            )
            compiled_artifact = next(
                item for item in installed["artifacts"] if item.get("kind") == "disease_variant"
            )
            review = compiled_artifact["catalog_review"]
            assert review["status"] == "approved"
            assert review["reviewed_content_hash"] != "0" * 64
            assert review["decisions"][0]["reviewer"] == "system:local"
            assert review["decisions"][0]["reviewer"] != "caller:forged-review"
            assert compiled_artifact["selection_contract"]["status"] == "not_applicable"
            assert compiled_artifact["source_citations"][0]["chunk_id"] == source_chunk_id
            actor = await _actor(server, campaign["id"])
            _, response = await _expose_disease(
                server, campaign["id"], actor["id"], "sewer_plague", "imported-disease-exposure"
            )
            result = response["result"]
            assert result["status"] == "infected"
            assert result["save"]["dc"] == 30
            receipt = result["disease_state"]["variant_receipt"]
            assert receipt["pack_id"] == "campaign.imported_disease"
            assert receipt["artifact_id"] == compiled_artifact["id"]
            assert receipt["source_citations"][0]["chunk_id"] == source_chunk_id
            assert any(
                item.get("facts", {}).get("disease_variant") == receipt
                for item in response["rule_receipts"]
            )
            assert result["disease_state"]["symptoms_due_elapsed_ticks"] == 28_800
        finally:
            close_server(server)

    asyncio.run(exercise())
