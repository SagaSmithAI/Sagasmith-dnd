from __future__ import annotations

import pytest

from sagasmith_dnd.resources import resize_bounded_resource


def test_resize_bounded_resource_preserves_spent_capacity() -> None:
    resource = {"value": 1, "max": 2}

    increased = resize_bounded_resource(resource, maximum=4)

    assert increased == {
        "before": 1,
        "after": 3,
        "old_max": 2,
        "new_max": 4,
        "unlimited": False,
    }
    assert resource == {"value": 3, "max": 4, "unlimited": False}

    decreased = resize_bounded_resource(resource, maximum=2)

    assert decreased["after"] == 2
    assert resource == {"value": 2, "max": 2, "unlimited": False}


def test_resize_bounded_resource_initializes_new_capacity_and_unlimited_state() -> None:
    resource: dict[str, object] = {}

    resize_bounded_resource(resource, maximum=3)
    assert resource == {"value": 3, "max": 3, "unlimited": False}

    resize_bounded_resource(resource, maximum=9, unlimited=True)
    assert resource == {"value": 0, "max": 0, "unlimited": True}


def test_resize_bounded_resource_validates_existing_bounds() -> None:
    with pytest.raises(ValueError, match="bounds"):
        resize_bounded_resource({"value": 3, "max": 2}, maximum=4)
