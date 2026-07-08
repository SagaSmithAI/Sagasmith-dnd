from sagasmith_dnd.rulesets import _schema_errors, get_ruleset, ruleset_schema, validate_ruleset


def test_ruleset_schema_is_packaged_and_validates_builtin_rulesets() -> None:
    schema = ruleset_schema()

    assert schema["$id"] == "https://sagasmith.ai/schemas/dnd/ruleset.schema.json"
    assert "activities" in schema["required"]
    assert validate_ruleset("dnd5e-2014")["valid"] is True
    assert validate_ruleset("dnd5e-2024")["valid"] is True


def test_ruleset_schema_rejects_missing_required_fields() -> None:
    errors = _schema_errors({"id": "broken"}, ruleset_schema())

    assert "$.name: missing required field" in errors
    assert "$.activities: missing required field" in errors


def test_ruleset_validator_reports_cross_reference_errors(monkeypatch) -> None:
    invalid = get_ruleset("dnd5e-2014")
    invalid["activities"]["bad_action"] = {
        "activation": "interrupt",
        "type": "mystery",
        "uses": {"resource": "bad", "cost": 1, "recovery": ["moonrise"]},
    }
    invalid["conditionTypes"]["bad_condition"] = {
        "statuses": ["missing_condition"],
        "riders": ["missing_rider"],
    }
    invalid["conditionEffects"]["bad_effect"] = ["missing_effect_condition"]

    monkeypatch.setattr("sagasmith_dnd.rulesets.get_ruleset", lambda _ruleset_id=None: invalid)
    result = validate_ruleset("dnd5e-2014")

    assert result["valid"] is False
    assert "activities.bad_action.activation: unknown activation 'interrupt'" in result["errors"]
    assert "activities.bad_action.type: unknown activity type 'mystery'" in result["errors"]
    assert "activities.bad_action.uses.recovery: unknown period 'moonrise'" in result["errors"]
    assert "conditionTypes.bad_condition.statuses: unknown condition 'missing_condition'" in result["errors"]
    assert "conditionTypes.bad_condition.riders: unknown condition 'missing_rider'" in result["errors"]
    assert "conditionEffects.bad_effect: unknown condition 'missing_effect_condition'" in result["errors"]
