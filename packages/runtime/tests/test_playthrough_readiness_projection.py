from copy import deepcopy
from types import SimpleNamespace

from sagasmith_dnd.playthrough import new_playthrough_manifest, validate_playthrough_manifest
from sagasmith_dnd_runtime.services.shared import SharedService


def test_ready_manifest_remains_readable_after_last_member_dies():
    manifest = new_playthrough_manifest(
        run_id="run", campaign_line_id="line", module_ids=["module"],
        recommended_party_minimum=None, recommended_party_maximum=None,
        selected_party_size=None, source_refs=[],
    )
    member = {
        "actor_id": "hero", "name": "Hero", "status": "active", "source": "generated",
        "source_asset_path": "", "level": 1, "xp": 0,
        "hit_points": {"current": 8, "maximum": 8}, "resources": {}, "wallet": {},
        "equipment": [], "knowledge_scope_actor_id": "hero",
    }
    manifest["party"]["members"] = [member]
    manifest["status"] = "ready"
    manifest = validate_playthrough_manifest(manifest)
    before = deepcopy(manifest)
    dead = deepcopy(manifest["party"]["members"][0])
    dead["status"] = "dead"
    dead["hit_points"]["current"] = 0
    runtime = {
        "party_members": [dead], "npcs": [], "snapshot_dag": manifest["snapshot_dag"],
        "random_stream": manifest["random_stream"], "current_scene": None, "world_state": {},
    }
    service = SimpleNamespace(playthrough_runtime_projection=lambda *_: runtime)
    projected = SharedService.sync_playthrough_manifest(service, "campaign", manifest)
    assert projected["status"] == "lobby"
    assert projected["party"]["members"][0]["status"] == "dead"
    assert projected["ending"] == before["ending"]
    assert manifest == before
    runtime["party_members"] = before["party"]["members"]
    restored = SharedService.sync_playthrough_manifest(service, "campaign", projected)
    assert restored["status"] == "ready"
