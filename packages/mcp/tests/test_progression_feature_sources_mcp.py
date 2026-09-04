"""Source identity regression using private synthetic printings, not book text."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.content_validation import build_selection_contract

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import import_and_activate_addon_fixture


async def _call(server, name, arguments):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _artifact(pack, kind, slug, card):
    artifact = {
        "id": f"{pack}.{kind}.{slug}",
        "kind": kind,
        "application_state": "selection_ready",
        "mechanical_scope": "descriptive",
        "execution_state": "descriptive_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "descriptive",
            "first_use_compilation_required": False,
        },
        "card": card,
        "rule_refs": [f"book:fixture:{pack}:{slug}"],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=artifact["rule_refs"],
    )
    return artifact


def _printing(pack):
    return [
        _artifact(pack, "feature", "neutral-trait", {"name": "Neutral Trait"}),
        _artifact(
            pack,
            "class",
            "fighter",
            {
                "name": "Fighter",
                "class_definition": {
                    "hit_die": 10,
                    "saving_throw_proficiencies": ["strength", "constitution"],
                    "armor_proficiencies": ["all armor", "shields"],
                    "weapon_proficiencies": ["simple weapons", "martial weapons"],
                    "tool_proficiencies": [],
                    "skill_choice_count": 2,
                    "skill_options": ["athletics", "perception", "survival"],
                },
            },
        ),
        _artifact(
            pack,
            "subclass",
            "fixture-guard",
            {
                "name": "Fixture Guard",
                "class_name": "Fighter",
                "minimum_level": 1,
            },
        ),
        _artifact(
            pack,
            "feature",
            "class-training",
            {
                "name": "Class Training",
                "class_name": "Fighter",
                "minimum_level": 1,
            },
        ),
        _artifact(
            pack,
            "feature",
            "guard-training",
            {
                "name": "Guard Training",
                "class_name": "Fighter",
                "subclass_name": "Fixture Guard",
                "minimum_level": 1,
            },
        ),
    ]


@pytest.mark.fresh_database
@pytest.mark.parametrize("chosen", ["first", "second"])
def test_progression_printings_are_source_bound_without_hiding_new_features(tmp_path, chosen):
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise(server):
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Source printings",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        revision = campaign["revision"]
        for printing in ("first", "second"):
            pack = f"dnd5e.addon.printing-{printing}"
            artifacts = _printing(pack)
            if printing == "second":
                artifacts += [
                    _artifact(
                        pack,
                        "feature",
                        "independent-training",
                        {
                            "name": "Independent Training",
                            "class_name": "Fighter",
                            "minimum_level": 1,
                        },
                    ),
                    _artifact(
                        pack,
                        "feature",
                        "independent-guard",
                        {
                            "name": "Independent Guard",
                            "class_name": "Fighter",
                            "subclass_name": "Fixture Guard",
                            "minimum_level": 1,
                        },
                    ),
                ]
            activated = await import_and_activate_addon_fixture(
                _call,
                server,
                campaign["id"],
                config.home,
                manifest={
                    "id": pack,
                    "version": "1.0.0",
                    "title": printing,
                    "namespace": pack,
                    "system_id": "dnd5e",
                    "editions": ["2014"],
                    "capabilities": [],
                },
                artifacts=artifacts,
                mechanics=[],
                expected_revision=revision,
                request_key=printing,
            )
            revision = activated["campaign_revision"]
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Source-bound fighter",
                    "sheet": default_character_sheet(),
                },
                "idempotency_key": "character",
            },
        )
        source = f"dnd5e.addon.printing-{chosen}"
        other = f"dnd5e.addon.printing-{'second' if chosen == 'first' else 'first'}"

        async def apply(artifact_id, key, selection=None):
            nonlocal character
            applied = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": artifact_id,
                    "selection": selection or {},
                    "expected_revision": character["revision"],
                    "idempotency_key": key,
                },
            )
            character = {
                "id": character["id"],
                "revision": applied["revision"],
                "sheet": applied["sheet"],
            }
            return applied

        await apply(f"{source}.class.fighter", "class", {"skills": ["athletics", "perception"]})
        await apply(f"{source}.subclass.fixture-guard", "subclass")
        plan = await _call(
            server,
            "character_query",
            {
                "view": "advancement",
                "payload": {
                    "character_id": character["id"],
                    "class_name": "Fighter",
                },
            },
        )
        offered = {item["artifact_id"] for item in plan["follow_up"]["feature_artifacts"]}
        for slug in ("class-training", "guard-training"):
            assert f"{source}.feature.{slug}" in offered
            assert f"{other}.feature.{slug}" not in offered
        for slug in ("independent-training", "independent-guard"):
            assert f"dnd5e.addon.printing-second.feature.{slug}" in offered

        for when in ("before", "after"):
            before = deepcopy(character)
            for slug in ("class-training", "guard-training"):
                with pytest.raises(Exception, match="selected.*source|source.*selected"):
                    await apply(f"{other}.feature.{slug}", f"reject-{when}-{slug}")
            with pytest.raises(Exception, match="source-bound subclass"):
                await apply(f"{other}.subclass.fixture-guard", f"reject-{when}-subclass")
            current = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": character["id"]},
                },
            )
            assert current["revision"] == before["revision"]
            assert current["sheet"] == before["sheet"]
            if when == "before":
                for slug in ("class-training", "guard-training"):
                    await apply(f"{source}.feature.{slug}", f"apply-{slug}")
        for slug in ("independent-training", "independent-guard"):
            await apply(f"dnd5e.addon.printing-second.feature.{slug}", f"apply-{slug}")
        # The source preference is not a global same-name feature prohibition.
        await apply(f"{source}.feature.neutral-trait", "neutral-source")
        await apply(f"{other}.feature.neutral-trait", "neutral-other")

        # A named imported subclass is not enough to choose between two printings.
        unbound = deepcopy(character["sheet"])
        unbound["content"]["features"] = []
        unbound["content"]["selections"] = [
            item for item in unbound["content"]["selections"] if item["kind"] != "subclass"
        ]
        imported = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Unbound printing",
                    "sheet": unbound,
                },
                "idempotency_key": "unbound-character",
            },
        )
        with pytest.raises(Exception, match="ambiguous.*source|source.*ambiguous"):
            await _call(
                server,
                "character_query",
                {
                    "view": "advancement",
                    "payload": {
                        "character_id": imported["id"],
                        "class_name": "Fighter",
                    },
                },
            )

    server = create_server(config)
    try:
        asyncio.run(exercise(server))
    finally:
        close_server(server)
