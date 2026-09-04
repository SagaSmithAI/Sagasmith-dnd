from copy import deepcopy

import pytest

from sagasmith_dnd.ground_items import validate_ground_items


def _item(item_id: str, *, kind: str = "equipment", container_id: str | None = None) -> dict:
    item = {
        "id": item_id,
        "name": item_id,
        "kind": kind,
        "quantity": 1,
        "weight_oz": 1,
        "price_cp": 1,
        "description": "",
        "source_key": "test",
        "container_id": container_id,
        "equipped": False,
        "equipped_slot": None,
        "identified": True,
        "attunement": "none",
        "condition": "normal",
        "uses": {},
        "charges": {},
        "mechanics": {},
        "ruling_requirements": [],
    }
    if kind == "container":
        item["mechanics"] = {
            "capacity_oz": 100,
            "weightless_contents": False,
            "extra_dimensional": False,
        }
    return item


def _record(*, location: dict | None = None, items: list[dict] | None = None) -> dict:
    return {
        "id": "ground-1",
        "source_actor_id": "actor-1",
        "scene_id": None,
        "encounter_id": "encounter-1",
        "campaign_revision": 4,
        "location": location or {"mode": "grid", "position": {"x": 2, "y": -1}},
        "root_item_id": "pack",
        "items": items or [_item("pack", kind="container"), _item("gem", container_id="pack")],
    }


def test_ground_items_normalizes_nested_container_and_preserves_attunement() -> None:
    record = _record()
    record["items"][1]["attunement"] = "required"
    before = deepcopy(record)
    result = validate_ground_items([record])
    assert record == before
    assert result[0]["location"] == {"mode": "grid", "position": {"x": 2, "y": -1}}
    assert result[0]["items"][1]["attunement"] == "required"
    assert result[0]["items"][1]["container_id"] == "pack"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update({"id": ""}),
        lambda record: record.update({"campaign_revision": True}),
        lambda record: record.update(
            {"location": {"mode": "grid", "position": {"x": 1.0, "y": 2}}}
        ),
        lambda record: record.update({"location": {"mode": "agent", "anchor_actor_id": "other"}}),
        lambda record: record.update({"root_item_id": "missing"}),
        lambda record: record.update({"items": [_item("pack", kind="container"), _item("pack")]}),
        lambda record: record.update(
            {
                "items": [
                    _item("pack", kind="container", container_id="gem"),
                    _item("gem", container_id="pack"),
                ]
            }
        ),
        lambda record: record.update(
            {
                "items": [
                    _item("pack", kind="container"),
                    {**_item("gem", container_id="pack"), "equipped": True},
                ]
            }
        ),
    ],
)
def test_ground_items_rejects_invalid_records_without_mutation(mutate) -> None:
    record = _record()
    mutate(record)
    before = deepcopy(record)
    with pytest.raises(ValueError):
        validate_ground_items([record])
    assert record == before


def test_ground_items_accepts_agent_anchor_and_rejects_extra_location_fields() -> None:
    result = validate_ground_items(
        [_record(location={"mode": "agent", "anchor_actor_id": "actor-1"})]
    )
    assert result[0]["location"] == {"mode": "agent", "anchor_actor_id": "actor-1"}
    invalid = _record()
    invalid["location"]["position"]["z"] = 0
    with pytest.raises(ValueError):
        validate_ground_items([invalid])


def test_ground_items_rejects_duplicate_record_ids() -> None:
    first = _record()
    second = deepcopy(first)
    second["scene_id"] = "scene-2"
    with pytest.raises(ValueError, match="duplicate"):
        validate_ground_items([first, second])
