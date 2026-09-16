from __future__ import annotations

import asyncio

import pytest
from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd_runtime.contracts import ACTION_PAYLOADS, action_parameters

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


def test_action_schemas_are_valid_and_reject_wrong_action_payloads() -> None:
    for tool, actions in ACTION_PAYLOADS.items():
        for action, model in actions.items():
            schema = model.model_json_schema()
            Draft202012Validator.check_schema(schema)
            assert schema["additionalProperties"] is False, (tool, action)
            assert all("type" in value or "anyOf" in value
                       for value in schema["properties"].values()), (tool, action)
    schema = action_parameters("combat_choice", {
        "type": "object", "properties": {"actor_id": {}},
    })
    validator = Draft202012Validator(schema)
    validator.validate({"actor_id": "hero", "action": "resolve_defense",
                        "payload": {"choice_id": "choice", "selection": {"decline": True}}})
    assert list(validator.iter_errors({"actor_id": "hero", "action": "resolve_defense",
                                      "payload": {"choice_id": "choice"}}))


@pytest.mark.parametrize("tool,arguments,field", [
    ("combat_choice", {"actor_id": "hero", "action": "resolve_defense",
                       "payload": {"choice_id": "choice"}}, "selection"),
    ("combat_choice", {"actor_id": "hero", "action": "resolve",
                       "payload": {"choice_id": "choice", "selection": "pass"}}, "selection"),
    ("combat_ready", {"action": "resolve_spell", "payload": {
        "actor_id": "hero", "choice_id": "choice", "release": "false"}}, "release"),
    ("combat_hp_change", {"target_id": "hero", "action": "heal",
                          "payload": {"amount": True}}, "amount"),
    ("combat_movement", {"actor_id": "hero", "action": "stand",
                         "payload": {"distance": 5}}, "distance"),
])
def test_invalid_payload_fails_before_campaign_lookup(tmp_path, tool, arguments, field) -> None:
    server = create_server(McpConfig(
        home=tmp_path, database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=tmp_path / "skills", modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    ))
    try:
        with pytest.raises(ToolError, match=field):
            asyncio.run(server.call_tool(tool, {"campaign_id": "not-created", **arguments}))
    finally:
        close_server(server)
