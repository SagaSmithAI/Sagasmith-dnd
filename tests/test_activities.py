import pytest

from sagasmith_dnd.activities import (
    ActivityError,
    consume_activity,
    recharge_activities_at_turn_start,
)
from sagasmith_dnd.character_schema import (
    consume_weapon_limited_use,
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.rule_engine import resolution_context


def test_activity_consumes_its_shared_resource_without_inventing_an_effect() -> None:
    sheet = default_character_sheet()
    sheet["resources"]["second_wind"] = {
        "label": "Second Wind",
        "value": 1,
        "max": 1,
        "recovers_on": "short_rest",
        "source_key": "fighter",
    }
    sheet["content"]["features"] = [
        {
            "id": "second-wind",
            "name": "Second Wind",
            "source_key": "fighter",
            "description": "Recover hit points.",
            "uses": {},
            "resource_key": "second_wind",
            "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
            "scaling": [],
            "choices": {"healing": "DM rolls by level"},
        }
    ]
    result = consume_activity(sheet, activity_id="second-wind")
    assert result["sheet"]["resources"]["second_wind"]["value"] == 0
    assert result["requires_ruling"] is True
    assert result["ruling_requirement"] == {
        "default_resolver": "agent",
        "ruling_kind": "agent_dm_adjudication",
        "source_excerpt": "",
    }
    assert result["payment"] == {"kind": "resource", "key": "second_wind", "amount": 1}


def test_activity_rejects_passive_and_exhausted_cards() -> None:
    sheet = default_character_sheet()
    sheet["content"]["activities"] = [
        {
            "id": "passive",
            "name": "Passive",
            "source_key": "test",
            "description": "",
            "uses": {},
            "resource_key": "",
            "activation": {"type": "passive", "cost": 0, "trigger": ""},
            "scaling": [],
            "choices": {},
        }
    ]
    with pytest.raises(ActivityError, match="passive"):
        consume_activity(sheet, activity_id="passive")


def test_activity_distinguishes_zero_capacity_from_unlimited_uses() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "no-divine-sense",
            "name": "Divine Sense",
            "uses": {"value": 0, "max": 0, "unlimited": False},
            "activation": {"type": "action", "cost": 1},
        },
        {
            "id": "archdruid-wild-shape",
            "name": "Wild Shape",
            "uses": {"value": 0, "max": 0, "unlimited": True},
            "activation": {"type": "action", "cost": 1},
        },
    ]

    with pytest.raises(ActivityError, match="exhausted"):
        consume_activity(sheet, activity_id="no-divine-sense")

    result = consume_activity(sheet, activity_id="archdruid-wild-shape")
    assert result["payment"] is None
    assert result["sheet"]["content"]["features"][1]["uses"]["unlimited"] is True


def test_zero_capacity_without_an_explicit_unlimited_flag_fails_closed() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "legacy-empty-resource",
            "name": "Legacy Empty Resource",
            "uses": {"value": 0, "max": 0},
            "activation": {"type": "action", "cost": 1},
        }
    ]

    with pytest.raises(ActivityError, match="exhausted"):
        consume_activity(sheet, activity_id="legacy-empty-resource")


def test_omitted_uses_remains_unlimited_after_card_validation() -> None:
    sheet = default_character_sheet()
    sheet["content"]["activities"] = [
        {
            "id": "source-action",
            "name": "Source Action",
            "source_key": "module-page",
            "activation": {"type": "action", "cost": 1},
        }
    ]

    validated = validate_character_sheet(sheet)
    result = consume_activity(validated, activity_id="source-action")

    assert validated["content"]["activities"][0]["uses"]["unlimited"] is True
    assert result["status"] == "committed"
    assert result["payment"] is None


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def test_recharge_activities_roll_only_while_unavailable() -> None:
    sheet = default_character_sheet()
    sheet["content"]["activities"] = [
        {
            "id": "lightning-strike-recharge-5-6-action",
            "name": "Lightning Strike (Recharge 5-6)",
            "source_key": "monster-manual-2014:p157",
            "activation": {"type": "action", "cost": 1},
            "uses": {
                "label": "Lightning Strike (Recharge 5-6)",
                "value": 0,
                "max": 1,
                "recovers_on": "manual",
                "source_key": "monster-manual-2014:p157",
            },
            "choices": {
                "recharge": {
                    "kind": "d6_turn_start",
                    "minimum": 5,
                    "maximum": 6,
                    "source_marker": "(Recharge 5-6)",
                }
            },
        }
    ]
    validated = validate_character_sheet(sheet)

    failed = recharge_activities_at_turn_start(
        validated,
        rng=_SequenceRng(4),
    )
    assert failed["sheet"]["content"]["activities"][0]["uses"]["value"] == 0
    assert failed["results"][0]["recharged"] is False

    recovered = recharge_activities_at_turn_start(
        failed["sheet"],
        rng=_SequenceRng(5),
    )
    assert recovered["sheet"]["content"]["activities"][0]["uses"]["value"] == 1
    assert recovered["results"][0]["recharged"] is True

    # An available action does not roll at all.
    available = recharge_activities_at_turn_start(
        recovered["sheet"],
        rng=_SequenceRng(),
    )
    assert available["results"] == []


def test_2024_recharge_uses_the_same_source_defined_d6_contract() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["content"]["activities"] = [
        {
            "id": "breath-recharge-5-6-action",
            "name": "Breath (Recharge 5-6)",
            "source_key": "bundled:srd2024/dragon",
            "activation": {"type": "action", "cost": 1},
            "uses": {
                "label": "Breath (Recharge 5-6)",
                "value": 0,
                "max": 1,
                "recovers_on": "manual",
                "source_key": "bundled:srd2024/dragon",
            },
            "choices": {
                "recharge": {
                    "kind": "d6_turn_start",
                    "minimum": 5,
                    "maximum": 6,
                    "source_marker": "(Recharge 5-6)",
                }
            },
        }
    ]
    rules = resolution_context(
        {
            "edition": "2024",
            "fingerprint": "recharge-pack",
            "lock": [],
            "mechanics": [],
        }
    )

    result = recharge_activities_at_turn_start(
        validate_character_sheet(sheet), rules=rules, rng=_SequenceRng(6)
    )

    assert result["results"][0]["recharged"] is True
    assert result["rule_receipts"][0]["mechanic_id"] == (
        "dnd5e.core.activity.recharge"
    )
    assert result["rule_receipts"][0]["citations"] == [
        {
            "source": "bundled:srd2024/DND5eSRD_253-272.md#limited-usage",
            "edition": "2024",
        }
    ]


def test_recharge_weapon_spends_and_recovers_the_same_bounded_use() -> None:
    sheet = default_character_sheet()
    sheet["inventory"]["items"] = [
        {
            "id": "web-recharge-5-6",
            "name": "Web (Recharge 5-6)",
            "kind": "weapon",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "",
                "damage_type": "",
                "attack_bonus_override": 5,
                "always_available": True,
                "recharge": {
                    "kind": "d6_turn_start",
                    "minimum": 5,
                    "maximum": 6,
                    "source_marker": "(Recharge 5-6)",
                },
            },
            "uses": {
                "label": "Web (Recharge 5-6)",
                "value": 1,
                "max": 1,
                "recovers_on": "manual",
                "source_key": "test:web",
            },
        }
    ]
    validated = validate_character_sheet(sheet)

    spent, audit = consume_weapon_limited_use(validated, "web-recharge-5-6")
    assert audit["remaining"] == 0
    assert derive_character_sheet(spent)["inventory"]["weapon_attacks"][0][
        "uses"
    ]["value"] == 0

    recovered = recharge_activities_at_turn_start(
        spent,
        rng=_SequenceRng(5),
    )
    assert recovered["results"][0]["source_card_kind"] == "item"
    assert recovered["results"][0]["recharged"] is True
    assert recovered["sheet"]["inventory"]["items"][0]["uses"]["value"] == 1
