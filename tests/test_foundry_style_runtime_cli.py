import json
import random
from pathlib import Path

from sagasmith_dnd.cli import main


def _call(capsys, *args: str):
    code = main([*args, "--json"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return json.loads(captured.out)["data"]


def test_foundry_style_scene_token_activity_and_periods(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    campaign = _call(capsys, "campaign", "start", "--name", "Foundry Runtime", "--edition", "2014")[
        "campaign"
    ]
    campaign_id = campaign["id"]

    ruleset = _call(capsys, "ruleset", "validate", "--id", "dnd5e-2014")
    assert ruleset["valid"] is True
    assert "activityActivationTypes" in ruleset

    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Cellar",
        "--width",
        "1000",
        "--height",
        "800",
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
        "hero",
        "--actor-type",
        "character",
    )
    goblin_token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Goblin",
        "--actor-id",
        "goblin",
        "--actor-type",
        "monster",
        "--x",
        "30",
        "--y",
        "0",
    )
    region = _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Web",
        "--shape",
        '{"type":"circle","x":10,"y":10,"radius":20}',
        "--behavior",
        "difficult_terrain",
        "--duration",
        '{"period":"declared_minute","value":10}',
    )
    assert region["behavior"] == "difficult_terrain"

    combat = _call(
        capsys,
        "combat",
        "start",
        "--campaign",
        campaign_id,
        "--scene",
        scene["id"],
        "--participants",
        json.dumps(
            [
                {
                    "id": "hero",
                    "token_id": hero_token["id"],
                    "name": "Hero",
                    "initiative": 20,
                    "ac": 16,
                    "hp": 20,
                    "features": ["action-surge", "second-wind", "extra-attack"],
                    "class_levels": {"fighter": 5},
                },
                {
                    "id": "goblin",
                    "token_id": goblin_token["id"],
                    "name": "Goblin",
                    "initiative": 10,
                    "ac": 12,
                    "hp": 7,
                },
            ]
        ),
    )
    assert combat["scene_id"] == scene["id"]
    assert "action_surge" in combat["legal_actions"]

    surged = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign_id,
        "--actor",
        "hero",
        "--activity",
        "action_surge",
    )
    hero = next(item for item in surged["combat"]["participants"] if item["id"] == "hero")
    assert hero["turn_budget"]["extra_actions"] == 1
    assert hero["turn_budget"]["bonus_actions"] == 1
    assert hero["turn_budget"]["reactions"] == 1

    healed = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign_id,
        "--actor",
        "hero",
        "--activity",
        "second_wind",
        "--target-id",
        "hero",
        "--payload",
        '{"fighter_level":5}',
    )
    hero = next(item for item in healed["combat"]["participants"] if item["id"] == "hero")
    assert hero["turn_budget"]["bonus_actions"] == 0
    assert hero["resources"]["second_wind"]["spent"] == 1

    rested = _call(capsys, "rest", "short", "--campaign", campaign_id)
    hero = next(item for item in rested["combat"]["participants"] if item["id"] == "hero")
    assert hero["resources"]["second_wind"]["spent"] == 0
    assert hero["resources"]["action_surge"]["spent"] == 0


def test_combat_act_is_disabled(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    campaign = _call(capsys, "campaign", "start", "--name", "No Free Mutate")["campaign"]
    _call(
        capsys,
        "combat",
        "start",
        "--campaign",
        campaign["id"],
        "--participants",
        '[{"id":"hero","name":"Hero","initiative":1}]',
    )
    code = main(["combat", "act", "--campaign", campaign["id"], "--payload", '{"x":1}', "--json"])
    captured = capsys.readouterr()
    assert code == 2
    assert "runtime_authority_required" in captured.out


def test_combat_start_can_derive_participants_from_scene_tokens(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite:///{tmp_path / 'scene-combat.db'}")
    campaign = _call(capsys, "campaign", "start", "--name", "Scene Combat")["campaign"]
    actor = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Mira",
        "--payload",
        '{"attributes":{"ac":{"value":15,"bonus":1},"hp":{"value":18,"max":20},"movement":{"walk":35}}}',
    )
    _call(capsys, "actor", "prepare", "--campaign", campaign["id"], "--actor", actor["id"])
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Road")
    token = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Mira Token",
        "--actor-id",
        actor["id"],
        "--x",
        "70",
        "--y",
        "140",
    )

    random.seed(0)
    combat = _call(capsys, "combat", "start", "--campaign", campaign["id"], "--scene", scene["id"])

    participant = combat["participants"][0]
    assert participant["id"] == actor["id"]
    assert participant["actor_id"] == actor["id"]
    assert participant["token_id"] == token["id"]
    assert participant["ac"] == 16
    assert participant["hp"] == 18
    assert participant["max_hp"] == 20
    assert participant["speed"] == 35
