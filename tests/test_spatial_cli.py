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
