import pytest
from jsonschema import ValidationError, validate
from sagasmith_dnd_runtime.result_contracts import tool_output_schema


@pytest.mark.parametrize("tool", ["character_check", "combat_preflight_attack",
                                 "combat_resolve_attack", "combat_cast_spell"])
def test_preflight_ruling_is_not_mistaken_for_malformed_committed_result(tool):
    schema = tool_output_schema(tool)
    pending = {
        "status": "pending_ruling", "committed": False,
        "ruling_kind": "agent_dm_adjudication", "default_resolver": "agent",
        "missing": ["distance_ft"], "reason": "Declare source-grounded spatial facts",
    }
    validate(pending, schema)
    with pytest.raises(ValidationError):
        validate({**pending, "status": "committed"}, schema)
    with pytest.raises(ValidationError):
        validate({"status": "pending_ruling"}, schema)
