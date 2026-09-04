from copy import deepcopy

import pytest

from sagasmith_dnd.campaign_state import merge_reviewed_campaign_state


@pytest.mark.parametrize("replacement", [None, [], {}, [{"id": "forged-item"}]])
def test_generic_campaign_patch_cannot_write_ground_items(replacement):
    current = {"ground_items": [{"id": "existing-item"}], "weather": "rain"}
    before = deepcopy(current)
    with pytest.raises(ValueError, match="system-owned.*ground_items"):
        merge_reviewed_campaign_state(current, {"ground_items": replacement})
    assert current == before


def test_narrative_campaign_patch_preserves_ground_items_without_aliasing():
    current = {"ground_items": [{"id": "existing-item"}], "weather": "rain"}
    updated = merge_reviewed_campaign_state(current, {"weather": "clear"})
    assert updated == {"ground_items": [{"id": "existing-item"}], "weather": "clear"}
    updated["ground_items"][0]["id"] = "changed-copy"
    assert current == {"ground_items": [{"id": "existing-item"}], "weather": "rain"}
