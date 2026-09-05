"""Fail-closed policy authority with public writes and isolated corruption probes."""

import asyncio
import json
import sqlite3
from copy import deepcopy

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import RulePackService, close_server, create_server
from scripts.regression_official_expansions import _ProtocolTools
from tests.test_official_expansions_mcp import _call
from tests.test_steel_defender_lifecycle_mcp import _create_bound_defender


def _config(tmp_path):
    return McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None,
        chroma_path_override=None, dnd_skills_dir=tmp_path / "skills",
        modulegen_skills_dir=tmp_path / "modulegen", auto_seed_rules=False,
    )


def _patch_source(monkeypatch, artifact_id, mutate):
    original = RulePackService.get_version

    def changed(self, pack_id, version):
        result = deepcopy(original(self, pack_id, version))
        for artifact in result.artifacts:
            if artifact["id"] == artifact_id:
                mutate(artifact["card"]["dependent_actor_template"])
        return result

    monkeypatch.setattr(RulePackService, "get_version", changed)


@pytest.mark.parametrize("policy", [None, {}, {"schema_version": 1, "owner_death": "default"}])
def test_missing_or_invalid_canonical_policy_cannot_create_actor(tmp_path, monkeypatch, policy):
    config = _config(tmp_path)

    async def exercise():
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign, owner, artifact = await _create_bound_defender(
                    server, config, instantiate=False,
                )
                owner = await _call(server, "character_query", {
                    "view": "get", "payload": {"character_id": owner["id"]},
                })

                def mutate(template):
                    if policy is None:
                        del template["lifecycle_policy"]
                    else:
                        template["lifecycle_policy"] = policy

                _patch_source(monkeypatch, artifact["id"], mutate)
                catalog = await _call(server, "character_query", {
                    "view": "catalog", "payload": {
                        "campaign_id": campaign["id"], "kind": "statblock",
                        "query": artifact["id"],
                    },
                })
                assert catalog[0]["selection_requirements"]["runtime_ready"] is False
                with pytest.raises(ToolError, match="lifecycle_policy"):
                    await server.call_tool("addon_actor_instantiate", {
                        "campaign_id": campaign["id"], "artifact_id": artifact["id"],
                        "owner_character_id": owner["id"],
                        "expected_revision": campaign["revision"], "idempotency_key": "missing",
                    })
                assert await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                }) == campaign
                assert await _call(server, "character_query", {
                    "view": "get", "payload": {"character_id": owner["id"]},
                }) == owner
                with sqlite3.connect(config.home / "data" / "ttrpgbase.db") as database:
                    assert database.execute("select count(*) from characters").fetchone()[0] == 1
        finally:
            close_server(runtime)

    asyncio.run(exercise())


@pytest.mark.parametrize("target", ["source", "binding", "authorization", "both"])
def test_policy_tampering_cannot_settle_owner_death(tmp_path, monkeypatch, target):
    config = _config(tmp_path)

    async def exercise():
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign, owner, defender = await _create_bound_defender(server, config)
                owner, defender = [await _call(server, "character_query", {
                    "view": "get", "payload": {"character_id": actor["id"]},
                }) for actor in (owner, defender)]
                path = config.home / "data" / "ttrpgbase.db"
                with sqlite3.connect(path) as database:
                    original = database.execute(
                        "select state from campaigns where id = ?", (campaign["id"],),
                    ).fetchone()[0]
                state = json.loads(original)
                relation = state["dependent_actor_relations"][0]
                if target == "source":
                    _patch_source(monkeypatch, relation["source_artifact_id"], lambda template:
                                  template["lifecycle_policy"].update(owner_death="perish"))
                else:
                    binding = relation["template_binding"]
                    if target in {"binding", "both"}:
                        binding["lifecycle_policy"]["owner_death"] = "perish"
                    if target in {"authorization", "both"}:
                        binding["authorization"]["lifecycle_policy"]["owner_death"] = "perish"
                    with sqlite3.connect(path) as database:
                        database.execute("update campaigns set state = ? where id = ?",
                                         (json.dumps(state), campaign["id"]))
                with sqlite3.connect(path) as database:
                    before = list(database.iterdump())
                with pytest.raises(ToolError, match="lifecycle_policy|receipt|signature"):
                    await server.call_tool("character_state_change", {
                        "character_id": owner["id"], "action": "damage",
                        "payload": {"parts": [{"amount": 1000, "damage_type": "force"}]},
                        "expected_revision": owner["revision"], "idempotency_key": "forged",
                    })
                with sqlite3.connect(path) as database:
                    assert list(database.iterdump()) == before
                    database.execute("update campaigns set state = ? where id = ?",
                                     (original, campaign["id"]))
                monkeypatch.undo()
                for actor in (owner, defender):
                    assert await _call(server, "character_query", {
                        "view": "get", "payload": {"character_id": actor["id"]},
                    }) == actor
        finally:
            close_server(runtime)

    asyncio.run(exercise())
