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
    hero = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Hero",
        "--payload",
        '{"attributes":{"ac":{"value":16},"hp":{"value":20,"max":20},"movement":{"walk":30}},"features":["action-surge","second-wind","extra-attack"],"class_levels":{"fighter":5}}',
    )
    goblin = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Goblin",
        "--type",
        "npc",
        "--payload",
        '{"attributes":{"ac":{"value":12},"hp":{"value":7,"max":7}}}',
    )
    surge_item = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign_id,
        "--actor",
        hero["id"],
        "--name",
        "Action Surge",
        "--type",
        "feat",
    )
    surge_activity = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        surge_item["id"],
        "--name",
        "Action Surge",
        "--type",
        "utility",
        "--payload",
        '{"activation":{"type":"free"},"uses":{"spent":0,"max":1,"cost":1,"recovery":["short_rest"]},"system":{"grant":{"extra_actions":1}}}',
    )
    wind_item = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign_id,
        "--actor",
        hero["id"],
        "--name",
        "Second Wind",
        "--type",
        "feat",
    )
    wind_activity = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        wind_item["id"],
        "--name",
        "Second Wind",
        "--type",
        "heal",
        "--payload",
        '{"activation":{"type":"bonus"},"uses":{"spent":0,"max":1,"cost":1,"recovery":["short_rest"]},"system":{"healing":"1"}}',
    )

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
        hero["id"],
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
        goblin["id"],
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
    )
    assert combat["scene_id"] == scene["id"]
    assert combat["combatants"][0]["token_id"] in {hero_token["id"], goblin_token["id"]}

    surged = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign_id,
        "--actor",
        hero["id"],
        "--item",
        surge_item["id"],
        "--activity",
        surge_activity["id"],
    )
    assert surged["state_delta"]["runtime"]["turn_budgets"][hero["id"]]["extra_action"] == 1
    assert surged["activity"]["uses"]["spent"] == 1

    healed = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign_id,
        "--actor",
        hero["id"],
        "--item",
        wind_item["id"],
        "--activity",
        wind_activity["id"],
        "--target-id",
        hero["id"],
    )
    assert healed["state_delta"]["runtime"]["turn_budgets"][hero["id"]]["bonus_action"] == 0
    assert healed["activity"]["uses"]["spent"] == 1

    rested = _call(capsys, "rest", "short", "--campaign", campaign_id)
    recovered = {
        item["activity_id"]
        for item in rested["document_recovery"]["recovered"]
        if item["type"] == "activity_uses"
    }
    assert recovered == {surge_activity["id"], wind_activity["id"]}


def test_combat_act_is_disabled(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    campaign = _call(capsys, "campaign", "start", "--name", "No Free Mutate")["campaign"]
    actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Hero")
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "No Free Mutate")
    _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Hero", "--actor-id", actor["id"])
    _call(
        capsys,
        "combat",
        "start",
        "--campaign",
        campaign["id"],
        "--scene",
        scene["id"],
    )
    code = main(["combat", "act", "--campaign", campaign["id"], "--payload", '{"x":1}', "--json"])
    captured = capsys.readouterr()
    assert code == 2
    assert "runtime_authority_required" in captured.out


def test_legacy_participants_and_ruleset_activity_paths_are_rejected(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite:///{tmp_path / 'legacy-reject.db'}")
    campaign = _call(capsys, "campaign", "start", "--name", "No Legacy Runtime")["campaign"]

    code = main(
        [
            "combat",
            "start",
            "--campaign",
            campaign["id"],
            "--participants",
            '[{"id":"hero","name":"Hero","initiative":1}]',
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "combat start no longer accepts free participants" in captured.out

    code = main(
        [
            "activity",
            "use",
            "--campaign",
            campaign["id"],
            "--actor",
            "hero",
            "--activity",
            "action_surge",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "activity use requires --item" in captured.out


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

    combatant = combat["combatants"][0]
    assert combatant["id"] == actor["id"]
    assert combatant["actor_id"] == actor["id"]
    assert combatant["token_id"] == token["id"]
    assert combatant["ac"] == 16
    assert combatant["hp"] == 18
    assert combatant["max_hp"] == 20
    assert combatant["speed"] == 35
