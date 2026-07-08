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


def test_cover_check_uses_cover_region_degree(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'cover.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Cover")["campaign"]
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Hall")
    attacker = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Archer",
        "--x",
        "0",
        "--y",
        "0",
    )
    target = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Guard",
        "--x",
        "100",
        "--y",
        "100",
    )
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Arrow Slit",
        "--behavior",
        "cover",
        "--shape",
        '{"type":"rect","x":90,"y":90,"width":30,"height":30}',
        "--metadata",
        '{"degree":"three_quarters"}',
    )

    result = _call(
        capsys,
        "cover",
        "check",
        "--scene",
        scene["id"],
        "--token",
        attacker["id"],
        "--target-id",
        target["id"],
    )

    assert result["cover"]["degree"] == "three_quarters"
    assert result["cover"]["ac_bonus"] == 5
    assert result["cover"]["dex_save_bonus"] == 5
    assert result["targetable"] is True


def test_activity_attack_applies_cover_to_target_ac(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'cover-attack.db').as_posix()}")
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: 10)
    campaign = _call(capsys, "campaign", "start", "--name", "Cover Attack")["campaign"]
    attacker_actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Archer")
    target_actor = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Guard",
        "--payload",
        '{"attributes":{"ac":{"value":12},"hp":{"value":10,"max":10}}}',
    )
    bow = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        attacker_actor["id"],
        "--name",
        "Bow",
        "--type",
        "weapon",
    )
    shot = _call(
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
        '{"activation":{"type":"action"},"range":{"value":80},"system":{"attack_bonus":7}}',
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Hall")
    attacker = _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Archer", "--actor-id", attacker_actor["id"])
    target = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Guard",
        "--actor-id",
        target_actor["id"],
        "--x",
        "100",
        "--y",
        "100",
    )
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Arrow Slit",
        "--behavior",
        "cover",
        "--shape",
        '{"type":"rect","x":90,"y":90,"width":30,"height":30}',
        "--metadata",
        '{"degree":"three_quarters"}',
    )

    result = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        attacker_actor["id"],
        "--item",
        bow["id"],
        "--activity",
        shot["id"],
        "--target-id",
        target_actor["id"],
        "--actor-token",
        attacker["id"],
        "--target-token",
        target["id"],
    )

    assert result["execution"]["target_ac"] == 17
    assert result["execution"]["cover"]["cover"]["degree"] == "three_quarters"
    assert result["execution"]["hit"] is True


def test_activity_save_applies_cover_to_dexterity_save(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'cover-save.db').as_posix()}")
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: 5)
    campaign = _call(capsys, "campaign", "start", "--name", "Cover Save")["campaign"]
    caster = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Mage")
    target_actor = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Guard",
        "--payload",
        '{"abilities":{"dex":{"value":10}}}',
    )
    wand = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        caster["id"],
        "--name",
        "Wand",
        "--type",
        "spell",
    )
    blast = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        wand["id"],
        "--name",
        "Blast",
        "--type",
        "save",
        "--payload",
        '{"activation":{"type":"action"},"range":{"value":80},"system":{"save":{"ability":"dex","dc":{"value":10}}}}',
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Hall")
    caster_token = _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Mage", "--actor-id", caster["id"])
    target = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Guard",
        "--actor-id",
        target_actor["id"],
        "--x",
        "100",
        "--y",
        "100",
    )
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Arrow Slit",
        "--behavior",
        "cover",
        "--shape",
        '{"type":"rect","x":90,"y":90,"width":30,"height":30}',
        "--metadata",
        '{"degree":"three_quarters"}',
    )

    result = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        caster["id"],
        "--item",
        wand["id"],
        "--activity",
        blast["id"],
        "--target-id",
        target_actor["id"],
        "--actor-token",
        caster_token["id"],
        "--target-token",
        target["id"],
    )

    assert result["execution"]["cover"]["cover"]["dex_save_bonus"] == 5
    assert result["execution"]["bonus"] == 5
    assert result["execution"]["success"] is True


def test_activity_attack_rejects_total_cover(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'cover-total.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Total Cover")["campaign"]
    attacker_actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Archer")
    target_actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Guard")
    bow = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        attacker_actor["id"],
        "--name",
        "Bow",
        "--type",
        "weapon",
    )
    shot = _call(
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
        '{"activation":{"type":"action"},"range":{"value":80},"system":{"attack_bonus":99}}',
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Hall")
    attacker = _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Archer", "--actor-id", attacker_actor["id"])
    target = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Guard",
        "--actor-id",
        target_actor["id"],
        "--x",
        "100",
        "--y",
        "100",
    )
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Wall",
        "--behavior",
        "cover",
        "--shape",
        '{"type":"rect","x":90,"y":90,"width":30,"height":30}',
        "--metadata",
        '{"degree":"total"}',
    )

    error = _call_error(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        attacker_actor["id"],
        "--item",
        bow["id"],
        "--activity",
        shot["id"],
        "--target-id",
        target_actor["id"],
        "--actor-token",
        attacker["id"],
        "--target-token",
        target["id"],
    )
    assert "total cover" in error["message"]
