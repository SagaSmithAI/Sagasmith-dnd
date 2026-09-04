import asyncio
import hashlib
from pathlib import Path

import pytest
from sagasmith_core.indexed_source import rule_chunk_key
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.content_validation import build_catalog_review, build_selection_contract
from sagasmith_dnd.standard_feature_ids import CORE_WATCHERS_EYE_MECHANIC_ID

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import import_and_activate_addon_fixture


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _mapping_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key).casefold() for key in value} | {
            nested_key for item in value.values() for nested_key in _mapping_keys(item)
        }
    if isinstance(value, list):
        return {nested_key for item in value for nested_key in _mapping_keys(item)}
    return set()


def _review_decision(role: str) -> dict:
    return {
        "role": role,
        "reviewer": f"agent:watchers-eye-{role}",
        "method": "agent",
        "checks": {
            "identity": True,
            "classification": True,
            "entry_boundary": True,
            "references": True,
        },
        "notes": "Verified the bounded synthetic source fixture and exact source binding.",
    }


def _background_artifact(artifact_id: str, name: str, skills: list[str], citation: dict) -> dict:
    chunk_key = str(citation["chunk_key"])
    source_ref = f"{citation['source']}#chunk:{chunk_key}"
    excerpt = (
        "This synthetic fixture establishes familiarity with local civic enforcement, "
        "its outposts, and local criminal activity. It contains no numeric modifier and "
        "requires campaign-authored facts for any concrete person, place, or information."
    )
    artifact = {
        "id": artifact_id,
        "kind": "background",
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
        "execution_state": "ruling_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "agent_ruling",
            "first_use_compilation_required": False,
            "clause_ids": [f"{name.casefold().replace(' ', '-')}-source"],
        },
        "rule_clauses": [
            {
                "schema_version": 1,
                "id": f"{name.casefold().replace(' ', '-')}-source",
                "title": name,
                "scope": "mechanical",
                "source_citations": [
                    {
                        "source": citation["source"],
                        "source_ref": {"chunk_key": chunk_key},
                        "source_excerpt": excerpt,
                    }
                ],
                "settlement": {
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": "Concrete local facts remain campaign-authored.",
                },
            }
        ],
        "card": {
            "name": name,
            "skill_proficiencies": skills,
            "background_grants": {
                "skills": skills,
                "feature": "Watcher's Eye",
                "languages": [],
                "spell_list_expansion": [],
                "tools": [],
                "equipment_item_ids": [],
                "choices": {
                    "language_count": 0,
                    "language_options": [],
                    "allow_any_language": False,
                    "skill_choice_count": 0,
                    "skill_options": [],
                    "tool_choice_count": 0,
                    "tool_options": [],
                    "tool_option_groups": [],
                },
            },
            "ruling_requirements": [
                {
                    "kind": "source_bound_import_resolution",
                    "ruling_kind": "agent_dm_adjudication",
                    "default_resolver": "agent",
                    "policy_ref": "rule_clause.v1",
                    "reason": "Concrete local facts remain campaign-authored.",
                    "source_excerpt": excerpt,
                    "requires_external_input_only_for": [],
                }
            ],
        },
        "rule_refs": [source_ref],
        "source_refs": [
            {
                "source_key": str(citation["source_key"]),
                "chunk_key": chunk_key,
                "page": 1,
                "note": "Watcher's Eye feature evidence",
            }
        ],
        "source_citations": [citation],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=[f"rule-source-chunk:{chunk_key}"],
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[_review_decision("primary"), _review_decision("critic")],
    )
    return artifact


def test_scag_artifact_identity_and_source_binding_are_exact() -> None:
    source_text = "Synthetic source evidence for the exact Watcher's Eye identity."
    source_key = "fixture.watchers-eye-identity"
    chunk_key = rule_chunk_key(source_key, 0, 0, source_text)
    citation = {
        "source": f"rule-source:{source_key}",
        "source_key": source_key,
        "chunk_key": chunk_key,
        "source_checksum": hashlib.sha256(source_text.encode()).hexdigest(),
        "page_start": 1,
        "page_end": 1,
        "source_excerpt": source_text,
    }
    city_id = next(
        item
        for item in server_module.SCAG_WATCHERS_EYE_BACKGROUND_IDS
        if item.endswith(".city-watch")
    )
    artifact = _background_artifact(city_id, "City Watch", ["Athletics", "Insight"], citation)
    binding = server_module._watchers_eye_source_binding(artifact)
    assert binding is not None
    assert binding["artifact_id"] == city_id
    assert binding["feature_rule_ref"] == artifact["rule_refs"][0]

    spoof = dict(artifact)
    spoof["id"] = "dnd5e.addon.spoof.background.city-watch"
    assert server_module._watchers_eye_source_binding(spoof) is None


@pytest.mark.fresh_database
def test_scag_watchers_eye_is_source_bound_bounded_and_persistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Watcher's Eye", "idempotency_key": "watchers-eye-campaign"},
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "expected_revision": campaign["revision"],
                "idempotency_key": "watchers-eye-profile",
            },
        )
        request_key = "watchers-eye"
        names = ["City Watch", "Investigator"]
        source_text = "# Reviewed fixture\n\n" + "\n\n".join(
            f"## {name}\n\nMechanics and choices for {name} were reviewed for this fixture."
            for name in names
        )
        source_key = f"fixture.{request_key}"
        source_checksum = hashlib.sha256(source_text.encode()).hexdigest()
        chunk_key = rule_chunk_key(source_key, 0, 0, source_text)
        citation = {
            "source": f"rule-source:{source_key}",
            "source_key": source_key,
            "chunk_key": chunk_key,
            "source_checksum": source_checksum,
            "page_start": 1,
            "page_end": 1,
            "source_excerpt": source_text,
        }
        fixture_rule_pack_id = "dnd5e.addon.scag.watchers-eye-fixture"
        city_id = f"{fixture_rule_pack_id}.background.city-watch"
        investigator_id = f"{fixture_rule_pack_id}.background.investigator"
        monkeypatch.setattr(server_module, "SCAG_RULE_PACK_ID", fixture_rule_pack_id)
        monkeypatch.setattr(
            server_module,
            "SCAG_WATCHERS_EYE_BACKGROUND_IDS",
            frozenset({city_id, investigator_id}),
        )
        artifacts = [
            _background_artifact(city_id, "City Watch", ["Athletics", "Insight"], citation),
            _background_artifact(
                investigator_id,
                "Investigator",
                ["Investigation", "Insight"],
                citation,
            ),
        ]
        monkeypatch.setattr(server_module, "official_expansion_catalog", lambda edition=None: ())
        monkeypatch.setattr(server_module, "official_expansion_dependency_rebinds", lambda: ())
        fixture = await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": server_module.SCAG_OFFICIAL_ADDON_ID,
                "version": "9.9.9",
                "title": "Source-bound SCAG fixture",
                "namespace": fixture_rule_pack_id,
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=artifacts,
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key=request_key,
            rule_pack_id=fixture_rule_pack_id,
            rule_pack_version="1.0.0",
        )
        monkeypatch.setattr(
            server_module,
            "official_expansion_catalog",
            lambda edition=None: (
                {
                    "id": server_module.SCAG_OFFICIAL_ADDON_ID,
                    "version": "9.9.9",
                    "checksum": fixture["package"]["checksum"],
                    "archive_sha256": "0" * 64,
                    "publication_id": "scag2014-fixture",
                    "title": "Source-bound SCAG fixture",
                    "classification": "official_supplement",
                    "editions": ["2014"],
                    "content_summary": {"background": 2},
                    "selection_ready": 2,
                    "catalog_only": 0,
                },
            ),
        )

        incompatible_campaign = await _call(
            server,
            "campaign_create",
            {"name": "2024 incompatible", "idempotency_key": "watchers-eye-2024-campaign"},
        )
        incompatible_profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": incompatible_campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2024"},
                "expected_revision": incompatible_campaign["revision"],
                "idempotency_key": "watchers-eye-2024-profile",
            },
        )
        with pytest.raises(Exception, match="edition"):
            await _call(
                server,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": incompatible_campaign["id"],
                        "kind": "addon",
                        "addon_id": server_module.SCAG_OFFICIAL_ADDON_ID,
                        "version": "9.9.9",
                    },
                    "expected_revision": incompatible_profile["campaign_revision"],
                    "idempotency_key": "reject-watchers-eye-2024-activation",
                },
            )

        async def make_character(name: str) -> dict:
            return await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": name,
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": f"character:{name}",
                },
            )

        city = await make_character("City Officer")
        city = await _call(
            server,
            "character_content_apply",
            {
                "character_id": city["id"],
                "artifact_id": city_id,
                "selection": {},
                "expected_revision": city["revision"],
                "idempotency_key": "apply-city-watch",
            },
        )
        investigator = await make_character("Investigator")
        investigator = await _call(
            server,
            "character_content_apply",
            {
                "character_id": investigator["id"],
                "artifact_id": investigator_id,
                "selection": {},
                "expected_revision": investigator["revision"],
                "idempotency_key": "apply-investigator",
            },
        )
        assert city["sheet"]["skills"]["athletics"]["proficiency"] == "proficient"
        assert city["sheet"]["skills"]["investigation"]["proficiency"] == "none"
        assert investigator["sheet"]["skills"]["investigation"]["proficiency"] == "proficient"
        assert investigator["sheet"]["skills"]["athletics"]["proficiency"] == "none"
        city_feature = city["sheet"]["content"]["features"][0]
        investigator_feature = investigator["sheet"]["content"]["features"][0]
        assert city_feature["name"] == investigator_feature["name"] == "Watcher's Eye"
        assert city_feature["id"].startswith(city_id)
        assert investigator_feature["id"].startswith(investigator_id)
        assert city_feature["pack_id"] == server_module.SCAG_RULE_PACK_ID
        assert len(city_feature["rule_refs"]) == 1
        assert city_feature["rule_refs"][0].startswith(f"rule-source:{source_key}#chunk:")
        narrative = city_feature["choices"]["narrative_capability"]
        assert narrative["mechanic_id"] == CORE_WATCHERS_EYE_MECHANIC_ID
        assert _mapping_keys(narrative).isdisjoint({"bonus", "dc"})
        source_binding = narrative["source_binding"]
        assert source_binding["addon_id"] == server_module.SCAG_OFFICIAL_ADDON_ID
        assert source_binding["addon_checksum"] == fixture["package"]["checksum"]
        assert source_binding["feature_rule_ref"] in city_feature["rule_refs"]

        replacement = await make_character("Background Replacement")
        replacement = await _call(
            server,
            "character_content_apply",
            {
                "character_id": replacement["id"],
                "artifact_id": city_id,
                "selection": {},
                "expected_revision": replacement["revision"],
                "idempotency_key": "replacement-city-watch",
            },
        )
        # Whole-sheet ingress cannot erase an authorized background. An explicit
        # background replacement workflow is a separate, still-open requirement.
        with pytest.raises(Exception, match="authoritative background state cannot be removed"):
            await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": replacement["id"],
                    "sheet": default_character_sheet(),
                    "expected_revision": replacement["revision"],
                    "idempotency_key": "rebuild-background-selection",
                },
            )
        unchanged = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": replacement["id"]},
            },
        )
        assert unchanged["revision"] == replacement["revision"]
        assert unchanged["sheet"] == replacement["sheet"]

        city = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": city["id"],
                "sheet": city["sheet"],
                "expected_revision": city["revision"],
                "idempotency_key": "replace-city-sheet",
            },
        )
        spoof_sheet = default_character_sheet()
        spoof_sheet["content"]["features"].append(
            {
                "id": "spoof.watchers-eye",
                "name": "Watcher's Eye",
                "source_key": "City Watch",
                "description": "Title-only spoof.",
            }
        )
        spoof = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Spoof",
                    "sheet": spoof_sheet,
                },
                "idempotency_key": "character:spoof",
            },
        )
        current_campaign = await _call(
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
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "watchers-eye-play",
            },
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "campaign",
                "campaign_id": campaign["id"],
                "principal_id": "player:spoof",
                "payload": {"role": "player"},
            },
        )
        fact_payload = {
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
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "upsert",
                "payload": fact_payload,
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "watch-outpost-fact",
            },
        )
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        call_args = {
            "campaign_id": campaign["id"],
            "action": "source_feature",
            "payload": {
                "actor_id": city["id"],
                "feature_id": city_feature["id"],
                "capability": "watch_outpost",
                "settlement_ref": "location:waterdeep",
                "fact_key": fact_payload["fact_key"],
            },
            "expected_revision": current_campaign["revision"],
            "idempotency_key": "watchers-eye-granted",
        }
        granted = await _call(server, "character_check", call_args)
        assert granted["outcome"] == "granted"
        assert granted["detail"] == fact_payload["content"]
        assert await _call(server, "character_check", call_args) == granted

        with pytest.raises(Exception, match="cannot access campaign"):
            await _call(
                server,
                "character_check",
                {**call_args, "principal_id": "player:spoof", "idempotency_key": "denied"},
            )
        with pytest.raises(Exception, match="requires exactly"):
            await _call(
                server,
                "character_check",
                {
                    **call_args,
                    "payload": {**call_args["payload"], "bonus": 10},
                    "idempotency_key": "numeric-spoof",
                },
            )
        with pytest.raises(Exception, match="capability must be one of"):
            await _call(
                server,
                "character_check",
                {
                    **call_args,
                    "payload": {**call_args["payload"], "capability": "made_up_bonus"},
                    "idempotency_key": "malformed-capability",
                },
            )
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        with pytest.raises(Exception, match="exact official SCAG background"):
            await _call(
                server,
                "character_check",
                {
                    "campaign_id": campaign["id"],
                    "action": "source_feature",
                    "payload": {
                        "actor_id": spoof["id"],
                        "feature_id": "spoof.watchers-eye",
                        "capability": "watch_outpost",
                        "settlement_ref": "location:waterdeep",
                        "fact_key": fact_payload["fact_key"],
                    },
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": "title-spoof",
                },
            )
        unavailable_fact = {
            "fact_key": "location:waterdeep:recognition",
            "kind": "source_fact",
            "subject": "Waterdeep watch recognition",
            "subject_ref": "location:waterdeep",
            "predicate": "dnd5e.watchers_eye.recognition",
            "content": "This watch does not recognize the officer in the current scene.",
            "metadata": {
                "dnd5e_watchers_eye": {
                    "schema_version": 1,
                    "capability": "recognition",
                    "outcome": "unavailable",
                }
            },
            "disclosure_scope": "dm",
        }
        await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "upsert",
                "payload": unavailable_fact,
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "recognition-unavailable-fact",
            },
        )
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        unavailable = await _call(
            server,
            "character_check",
            {
                "campaign_id": campaign["id"],
                "action": "source_feature",
                "payload": {
                    "actor_id": city["id"],
                    "feature_id": city_feature["id"],
                    "capability": "recognition",
                    "settlement_ref": "location:waterdeep",
                    "fact_key": unavailable_fact["fact_key"],
                },
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "watchers-eye-unavailable",
            },
        )
        assert unavailable["outcome"] == "unavailable"
        assert unavailable["detail"] == unavailable_fact["content"]
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        pending_args = {
            "campaign_id": campaign["id"],
            "action": "source_feature",
            "payload": {
                "actor_id": city["id"],
                "feature_id": city_feature["id"],
                "capability": "watch_information",
                "settlement_ref": "location:waterdeep",
                "fact_key": "location:waterdeep:watch-information",
            },
            "expected_revision": current_campaign["revision"],
            "idempotency_key": "watchers-eye-pending",
        }
        pending = await _call(server, "character_check", pending_args)
        assert pending["outcome"] == "pending_gm_ruling"
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        with pytest.raises(Exception, match="revision conflict"):
            await _call(
                server,
                "character_check",
                {
                    **pending_args,
                    "expected_revision": current_campaign["revision"] - 1,
                    "idempotency_key": "watchers-eye-stale",
                },
            )
        receipts = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "receipts",
                "payload": {"mechanic_id": CORE_WATCHERS_EYE_MECHANIC_ID},
            },
        )
        assert {item["receipt"]["outcome"] for item in receipts} == {
            "granted",
            "unavailable",
            "pending_gm_ruling",
        }

        close_server(server)
        server = create_server(config)
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        restarted = await _call(
            server,
            "character_check",
            {
                **pending_args,
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "watchers-eye-after-restart",
            },
        )
        assert restarted["outcome"] == "pending_gm_ruling"
        reloaded = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": city["id"]}},
        )
        assert reloaded["sheet"]["content"]["features"][0]["id"] == city_feature["id"]
        close_server(server)

    asyncio.run(exercise())
