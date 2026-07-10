from __future__ import annotations

import json
from pathlib import Path

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code == 0, value
    assert value["ok"] is True
    return value["data"]


def _call_error(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code != 0, value
    assert value["ok"] is False
    return value["error"]


def test_token_move_reports_distance_and_difficult_terrain_cost(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'map.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Map")["campaign"]
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    token = _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Hero")
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Web",
        "--behavior",
        "difficult_terrain",
        "--shape",
        '{"type":"circle","x":70,"y":0,"radius":10}',
    )

    moved = _call(
        capsys,
        "token",
        "move",
        "--token",
        token["id"],
        "--x",
        "70",
        "--y",
        "0",
    )

    assert moved["x"] == 70
    assert moved["movement"]["distance"] == 5
    assert moved["movement"]["cost"] == 10
    assert moved["movement"]["regions"][0]["behavior"] == "difficult_terrain"


def test_token_update_changes_visibility_and_metadata_with_undo(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'token-update.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Token Update")["campaign"]
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Grid")
    token = _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Hero")

    updated = _call(
        capsys,
        "token",
        "update",
        "--token",
        token["id"],
        "--name",
        "Hidden Hero",
        "--hidden",
        "true",
        "--disposition",
        "friendly",
        "--vision",
        '{"darkvision":60}',
        "--metadata",
        '{"controlled":true}',
    )

    assert updated["name"] == "Hidden Hero"
    assert updated["hidden"] is True
    assert updated["disposition"] == "friendly"
    assert updated["vision"]["darkvision"] == 60
    assert updated["metadata"]["controlled"] is True

    _call(capsys, "state", "undo", "--campaign", campaign["id"])
    shown = _call(capsys, "token", "show", "--token", token["id"])
    assert shown["name"] == "Hero"
    assert shown["hidden"] is False


def test_scene_show_prepares_token_runtime_from_actor(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'scene-runtime.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Scene Runtime")["campaign"]
    actor = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Scout",
        "--payload",
        json.dumps(
            {
                "attributes": {
                    "hp": {"value": 7, "max": 12},
                    "senses": {"darkvision": 60},
                }
            }
        ),
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Cave")
    token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Scout",
        "--actor-id",
        actor["id"],
        "--width",
        "2",
        "--height",
        "1",
    )

    shown = _call(capsys, "scene", "show", "--scene", scene["id"])
    prepared = shown["tokens"][0]

    assert prepared["id"] == token["id"]
    assert prepared["runtime"]["actor"]["id"] == actor["id"]
    assert prepared["runtime"]["bars"]["bar1"]["value"] == 7
    assert prepared["runtime"]["bars"]["bar1"]["max"] == 12
    assert prepared["runtime"]["vision"]["sight"]["range"] == 60
    assert prepared["runtime"]["vision"]["sight"]["visionMode"] == "darkvision"
    assert prepared["runtime"]["size"]["pixels"]["width"] == 140


def test_scene_activate_advances_scene_end_durations(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'scene-active.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Scene Active")["campaign"]
    actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Hero")
    first = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Room One")
    second = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Room Two")
    effect = _call(
        capsys,
        "effect",
        "add",
        "--campaign",
        campaign["id"],
        "--actor",
        actor["id"],
        "--name",
        "Room Blessing",
        "--duration",
        '{"period":"scene_end"}',
    )

    _call(capsys, "scene", "activate", "--campaign", campaign["id"], "--scene", first["id"])
    activated = _call(capsys, "scene", "activate", "--campaign", campaign["id"], "--scene", second["id"])
    listed = _call(capsys, "effect", "list", "--campaign", campaign["id"], "--actor", actor["id"])

    assert activated["active_scene_id"] == second["id"]
    assert activated["previous_scene_id"] == first["id"]
    assert effect["id"] not in {item["id"] for item in listed["effects"]}


def test_token_move_leaving_reach_creates_opportunity_attack_window(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'opportunity.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Opportunity")["campaign"]
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    hero = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Hero",
        "--actor-id",
        "hero",
        "--disposition",
        "friendly",
        "--x",
        "70",
        "--y",
        "0",
    )
    _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Goblin",
        "--actor-id",
        "goblin",
        "--disposition",
        "hostile",
        "--x",
        "0",
        "--y",
        "0",
        "--metadata",
        '{"reach":5}',
    )

    moved = _call(
        capsys,
        "token",
        "move",
        "--token",
        hero["id"],
        "--x",
        "140",
        "--y",
        "0",
    )

    pending = moved["movement"]["pending"][0]
    assert pending["trigger"] == "opportunity_attack"
    assert pending["actor_id"] == "goblin"
    assert pending["target_actor_id"] == "hero"


def test_activity_range_uses_scene_token_distance(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'range.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Range")["campaign"]
    archer = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Archer")
    target = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Target", "--type", "npc")
    bow = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        archer["id"],
        "--name",
        "Shortbow",
        "--type",
        "weapon",
    )
    bow_shot = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        bow["id"],
        "--name",
        "Shot",
        "--type",
        "attack",
        "--payload",
        '{"activation":{"type":"action"},"range":{"value":30,"long":120},"system":{"attack_bonus":20}}',
    )
    sword = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        archer["id"],
        "--name",
        "Sword",
        "--type",
        "weapon",
    )
    slash = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        sword["id"],
        "--name",
        "Slash",
        "--type",
        "attack",
        "--payload",
        '{"activation":{"type":"action"},"range":{"value":5},"system":{"attack_bonus":20}}',
    )
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    archer_token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Archer",
        "--actor-id",
        archer["id"],
    )
    target_token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Target",
        "--actor-id",
        target["id"],
        "--x",
        "700",
        "--y",
        "0",
    )

    error = _call_error(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        archer["id"],
        "--item",
        sword["id"],
        "--activity",
        slash["id"],
        "--target-id",
        target["id"],
        "--actor-token",
        archer_token["id"],
        "--target-token",
        target_token["id"],
    )
    assert "out of range" in error["message"]

    ranged = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        archer["id"],
        "--item",
        bow["id"],
        "--activity",
        bow_shot["id"],
        "--target-id",
        target["id"],
        "--actor-token",
        archer_token["id"],
        "--target-token",
        target_token["id"],
    )
    assert ranged["execution"]["range"]["distance"] == 50
    assert ranged["execution"]["range"]["disadvantage"] is True


def test_ranged_attack_has_disadvantage_when_hostile_is_within_reach(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'range-threat.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Ranged Threat")["campaign"]
    archer = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Archer")
    target = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Target", "--type", "npc")
    bow = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        archer["id"],
        "--name",
        "Shortbow",
        "--type",
        "weapon",
    )
    bow_shot = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        bow["id"],
        "--name",
        "Shot",
        "--type",
        "attack",
        "--payload",
        '{"activation":{"type":"action"},"range":{"value":30,"long":120},"system":{"attack_bonus":20}}',
    )
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    archer_token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Archer",
        "--actor-id",
        archer["id"],
        "--disposition",
        "friendly",
    )
    target_token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Target",
        "--actor-id",
        target["id"],
        "--disposition",
        "hostile",
        "--x",
        "350",
        "--y",
        "0",
    )
    threat = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Threat",
        "--actor-id",
        "threat",
        "--disposition",
        "hostile",
        "--x",
        "70",
        "--y",
        "0",
        "--metadata",
        '{"reach":5}',
    )

    ranged = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        archer["id"],
        "--item",
        bow["id"],
        "--activity",
        bow_shot["id"],
        "--target-id",
        target["id"],
        "--actor-token",
        archer_token["id"],
        "--target-token",
        target_token["id"],
    )

    assert ranged["execution"]["range"]["distance"] == 25
    assert ranged["execution"]["range"]["hostile_within_reach"][0]["token_id"] == threat["id"]
    assert ranged["execution"]["disadvantage"] is True
    assert "range:hostile_within_reach" in ranged["execution"]["disadvantage_sources"]


def test_disengage_move_does_not_create_opportunity_attack_window(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'opportunity-disengage.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Disengage")["campaign"]
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    hero = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Hero",
        "--actor-id",
        "hero",
        "--disposition",
        "friendly",
        "--x",
        "70",
        "--y",
        "0",
    )
    _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Goblin",
        "--actor-id",
        "goblin",
        "--disposition",
        "hostile",
        "--x",
        "0",
        "--y",
        "0",
        "--metadata",
        '{"reach":5}',
    )

    moved = _call(
        capsys,
        "token",
        "move",
        "--token",
        hero["id"],
        "--x",
        "140",
        "--y",
        "0",
        "--metadata",
        '{"disengage":true}',
    )

    assert moved["movement"]["pending"] == []


def test_opportunity_attack_window_persists_and_resolves_activity(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'opportunity-resolve.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Opportunity Resolve")["campaign"]
    hero = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Hero")
    goblin = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Goblin", "--type", "npc")
    blade = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        goblin["id"],
        "--name",
        "Scimitar",
        "--type",
        "weapon",
    )
    slash = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        blade["id"],
        "--name",
        "Slash",
        "--type",
        "attack",
        "--payload",
        '{"activation":{"type":"action"},"range":{"value":5},"system":{"attack_bonus":20}}',
    )
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    hero_token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Hero",
        "--actor-id",
        hero["id"],
        "--disposition",
        "friendly",
        "--x",
        "70",
        "--y",
        "0",
    )
    _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Goblin",
        "--actor-id",
        goblin["id"],
        "--disposition",
        "hostile",
        "--x",
        "0",
        "--y",
        "0",
        "--metadata",
        '{"reach":5}',
    )

    moved = _call(
        capsys,
        "token",
        "move",
        "--token",
        hero_token["id"],
        "--x",
        "140",
        "--y",
        "0",
    )
    pending = moved["movement"]["pending"][0]
    assert pending["distance"] == 5
    assert pending["after_distance"] == 10

    listed = _call(capsys, "reaction", "list", "--campaign", campaign["id"], "--actor", goblin["id"])
    window = listed["pending"][0]
    resolved = _call(
        capsys,
        "reaction",
        "resolve",
        "--campaign",
        campaign["id"],
        "--id",
        window["id"],
        "--payload",
        f'{{"item_id":"{blade["id"]}","activity_id":"{slash["id"]}"}}',
    )

    assert resolved["reaction_result"]["payment"] == "reaction"
    assert resolved["reaction_result"]["execution"]["type"] == "attack"
    assert resolved["reaction_result"]["execution"]["range"]["distance"] == 5
    assert resolved["reaction_result"]["state_delta"]["runtime"]["turn_budgets"][goblin["id"]]["reaction"] == 0


def test_token_move_applies_and_removes_region_active_effect(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'region-effect.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Region Effect")["campaign"]
    actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Mira")
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
    )
    token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Hero",
        "--actor-id",
        actor["id"],
        "--x",
        "0",
        "--y",
        "0",
    )
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Blessing Aura",
        "--behavior",
        "apply_active_effect",
        "--shape",
        '{"type":"circle","x":70,"y":0,"radius":20}',
        "--metadata",
        '{"statuses":["blessed"],"changes":[{"key":"system.attributes.ac.bonus","mode":"ADD","value":1}]}',
    )

    entered = _call(capsys, "token", "move", "--token", token["id"], "--x", "70", "--y", "0")

    created = entered["movement"]["region_effects"]["created"]
    assert created[0]["name"] == "Blessing Aura"
    assert created[0]["actor_id"] == actor["id"]
    assert created[0]["statuses"] == ["blessed"]

    left = _call(capsys, "token", "move", "--token", token["id"], "--x", "140", "--y", "0")
    removed = left["movement"]["region_effects"]["removed"]
    assert removed[0]["id"] == created[0]["id"]
