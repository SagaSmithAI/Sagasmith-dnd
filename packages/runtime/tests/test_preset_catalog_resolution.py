from types import SimpleNamespace

import pytest
from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import build_dnd_content_actor
from sagasmith_dnd_runtime.services.characters import CharactersService


def test_campaign_preset_resolution_uses_catalog_version_not_all_installed_versions():
    def artifact(name):
        notes = default_character_notes()
        notes["profile"]["summary"] = name
        card = build_dnd_content_actor(
            actor_id="test.actor", version="1.0.0", actor_type="npc", name=name,
            sheet=default_character_sheet(), notes=notes,
        )
        return {"id": "test.catalog.actor", "kind": "actor_card", "card": {"content_actor": card}}

    old, current = artifact("Historical"), artifact("Current")
    calls = []

    def available(campaign_id, *, kind, branch_id):
        calls.append((campaign_id, kind, branch_id))
        return [("test.pack", "2.0.0", current)]

    service = SimpleNamespace(
        available_content_artifacts=available,
        rule_packs=SimpleNamespace(list_versions=lambda: [
            SimpleNamespace(status="installed", artifacts=[old]),
            SimpleNamespace(status="installed", artifacts=[current]),
        ]),
    )
    result = CharactersService.default_preset_actor_card(
        service, "test.catalog.actor", "campaign", "branch"
    )
    assert result["name"] == "Current"
    assert calls == [("campaign", "actor_card", "branch")]
    with pytest.raises(ValueError, match="exactly one"):
        CharactersService.default_preset_actor_card(service, "test.catalog.actor")
    service.available_content_artifacts = lambda *args, **kwargs: []
    with pytest.raises(ValueError, match="exactly one"):
        CharactersService.default_preset_actor_card(service, "test.catalog.actor", "campaign")
