from copy import deepcopy

import pytest

from sagasmith_dnd.dependent_actor_relations import (
    validate_dependent_actor_references,
    validate_dependent_actor_relations,
)


def relation(**overrides):
    value = {
        "owner_character_id": "owner-1",
        "dependent_actor_id": "defender-1",
        "relation_key": "steel_defender",
        "source_artifact_id": "artifact.steel-defender",
        "source_pack_id": "pack.eberron",
        "source_pack_version": "1.0.0",
        "status": "active",
        "created_campaign_revision": 3,
        "created_long_rest_elapsed_ticks": None,
        "death_elapsed_ticks": None,
        "revival_started_elapsed_ticks": None,
        "revival_completes_elapsed_ticks": None,
        "template_binding": {
            "owner_class_name": "artificer",
            "casting_slot_level": None,
            "template_variant": None,
            "numeric_parameters": {
                "owner_class_level": 5,
                "owner_proficiency_bonus": 3,
            },
            "reviewed_expression_hash": "a" * 64,
            "lifecycle_policy": {"schema_version": 1, "owner_death": "independent"},
            "authorization": {
                "schema_version": 1,
                "purpose": "dependent_actor_template",
                "campaign_id": "campaign-1",
                "owner_character_id": "owner-1",
                "dependent_actor_id": "defender-1",
                "relation_key": "steel_defender",
                "source_artifact_id": "artifact.steel-defender",
                "source_pack_id": "pack.eberron",
                "source_pack_version": "1.0.0",
                "owner_class_name": "artificer",
                "casting_slot_level": None,
                "template_variant": None,
                "numeric_parameters": {
                    "owner_class_level": 5,
                    "owner_proficiency_bonus": 3,
                },
                "reviewed_expression_hash": "a" * 64,
                "lifecycle_policy": {"schema_version": 1, "owner_death": "independent"},
                "signature": "b" * 64,
            },
        },
    }
    value.update(overrides)
    if value["status"] in {"dead", "replaced"} and "death_elapsed_ticks" not in overrides:
        value["death_elapsed_ticks"] = 10
    return value


def test_relations_are_deep_normalized_and_input_is_unchanged():
    value = [relation()]
    before = deepcopy(value)
    result = validate_dependent_actor_relations(value)
    assert result == value
    assert result is not value and result[0] is not value[0]
    assert value == before


@pytest.mark.parametrize(
    "field,bad",
    [
        ("owner_character_id", ""),
        ("dependent_actor_id", True),
        ("relation_key", 1),
        ("source_artifact_id", "x" * 501),
        ("source_pack_id", None),
        ("source_pack_version", " "),
        ("status", "pending"),
        ("created_campaign_revision", True),
        ("created_campaign_revision", -1),
        ("created_long_rest_elapsed_ticks", True),
        ("created_long_rest_elapsed_ticks", -1),
    ],
)
def test_invalid_field_is_rejected(field, bad):
    with pytest.raises(ValueError):
        validate_dependent_actor_relations([relation(**{field: bad})])


def test_unknown_fields_and_missing_fields_are_rejected():
    raw = relation()
    raw["extra"] = "nope"
    with pytest.raises(ValueError):
        validate_dependent_actor_relations([raw])
    raw = relation()
    del raw["source_pack_version"]
    with pytest.raises(ValueError):
        validate_dependent_actor_relations([raw])


def test_duplicate_dependent_and_multiple_active_owner_key_are_rejected():
    with pytest.raises(ValueError, match="duplicate dependent"):
        validate_dependent_actor_relations([relation(), relation(status="dead")])
    with pytest.raises(ValueError, match="multiple active"):
        validate_dependent_actor_relations(
            [relation(dependent_actor_id="defender-1"), relation(dependent_actor_id="defender-2")]
        )


def test_dead_and_replaced_history_may_share_owner_relation_key():
    result = validate_dependent_actor_relations(
        [relation(status="replaced"), relation(dependent_actor_id="defender-2")]
    )
    assert [item["status"] for item in result] == ["replaced", "active"]


def test_relation_text_is_trimmed_and_long_official_ids_are_supported():
    artifact_id = "dnd5e.addon." + "x" * 180
    result = validate_dependent_actor_relations(
        [relation(owner_character_id=" owner-1 ", source_artifact_id=artifact_id)]
    )
    assert result[0]["owner_character_id"] == "owner-1"
    assert result[0]["source_artifact_id"] == artifact_id


def test_references_require_existing_owner_and_dependent():
    result = validate_dependent_actor_references([relation()], {"owner-1", "defender-1"})
    assert result[0]["dependent_actor_id"] == "defender-1"
    with pytest.raises(ValueError, match="unknown owner"):
        validate_dependent_actor_references([relation()], ["defender-1"])
    with pytest.raises(ValueError, match="unknown dependent"):
        validate_dependent_actor_references([relation()], ["owner-1"])


@pytest.mark.parametrize("actor_ids", [None, [True], [""]])
def test_actor_id_set_is_strict(actor_ids):
    with pytest.raises(ValueError):
        validate_dependent_actor_references([relation()], actor_ids)


def test_creation_rest_tick_accepts_null_or_non_negative_integer():
    assert (
        validate_dependent_actor_relations([relation(created_long_rest_elapsed_ticks=0)])[0][
            "created_long_rest_elapsed_ticks"
        ]
        == 0
    )
    assert (
        validate_dependent_actor_relations([relation(created_long_rest_elapsed_ticks=None)])[0][
            "created_long_rest_elapsed_ticks"
        ]
        is None
    )


def test_creation_rest_tick_is_required_by_the_strict_schema():
    raw = relation()
    del raw["created_long_rest_elapsed_ticks"]
    with pytest.raises(ValueError, match="exactly the relation fields"):
        validate_dependent_actor_relations([raw])


def test_template_binding_is_required_and_strict():
    raw = relation()
    del raw["template_binding"]
    with pytest.raises(ValueError, match="exactly the relation fields"):
        validate_dependent_actor_relations([raw])

    raw = relation()
    raw["template_binding"]["reviewed_expression_hash"] = "not-a-hash"
    with pytest.raises(ValueError, match="SHA-256"):
        validate_dependent_actor_relations([raw])

    raw = relation()
    raw["template_binding"]["numeric_parameters"]["owner_proficiency_bonus"] = True
    with pytest.raises(ValueError, match="integer"):
        validate_dependent_actor_relations([raw])

    raw = relation()
    raw["template_binding"]["authorization"]["numeric_parameters"] = {
        "owner_proficiency_bonus": 4
    }
    with pytest.raises(ValueError, match="does not match binding"):
        validate_dependent_actor_relations([raw])
