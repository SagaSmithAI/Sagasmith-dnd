from sagasmith_dnd.rulesets import _schema_errors, get_ruleset, ruleset_schema, validate_ruleset


def test_ruleset_schema_is_packaged_and_validates_builtin_rulesets() -> None:
    schema = ruleset_schema()

    assert schema["$id"] == "https://sagasmith.ai/schemas/dnd/ruleset.schema.json"
    assert "abilities" in schema["required"]
    assert "actionEconomy" in schema["required"]
    assert "activities" in schema["required"]
    assert validate_ruleset("dnd5e-2014")["valid"] is True
    assert validate_ruleset("dnd5e-2024")["valid"] is True


def test_2014_ruleset_declares_core_runtime_baseline() -> None:
    ruleset = get_ruleset("dnd5e-2014")

    assert set(ruleset["abilities"]) == {"str", "dex", "con", "int", "wis", "cha"}
    assert ruleset["skills"]["prc"]["ability"] == "wis"
    assert set(ruleset["damageTypes"]) >= {"bludgeoning", "piercing", "slashing", "fire", "force"}
    assert ruleset["actionEconomy"]["turnBudget"] == {
        "main_action": 1,
        "bonus_action": 1,
        "reaction": 1,
        "movement": 1,
        "object_interaction": 1,
    }
    assert ruleset["range"]["beyondLongRange"] == "illegal"
    assert ruleset["cover"]["degrees"]["three_quarters"]["ac_bonus"] == 5
    assert ruleset["resources"]["action_surge"]["recovery"] == ["short_rest", "long_rest"]
    assert ruleset["effects"]["concentration"]["exclusivePerActor"] is True


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
    invalid["skills"]["bad_skill"] = {"ability": "luck"}
    invalid["resources"]["bad_resource"] = {"recovery": ["moonrise"]}
    invalid["effects"]["bad_duration"] = {"kind": "duration_strategy", "period": "moonrise"}
    invalid["actionEconomy"]["reactionRefresh"] = "moonrise"
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
    assert "activities.bad_action.uses.resource: unknown resource 'bad'" in result["errors"]
    assert "activities.bad_action.uses.recovery: unknown period 'moonrise'" in result["errors"]
    assert "skills.bad_skill.ability: unknown ability 'luck'" in result["errors"]
    assert "resources.bad_resource.recovery: unknown period 'moonrise'" in result["errors"]
    assert "effects.bad_duration.period: unknown period 'moonrise'" in result["errors"]
    assert "actionEconomy.reactionRefresh: unknown period 'moonrise'" in result["errors"]
    assert "conditionTypes.bad_condition.statuses: unknown condition 'missing_condition'" in result["errors"]
    assert "conditionTypes.bad_condition.riders: unknown condition 'missing_rider'" in result["errors"]
    assert "conditionEffects.bad_effect: unknown condition 'missing_effect_condition'" in result["errors"]
