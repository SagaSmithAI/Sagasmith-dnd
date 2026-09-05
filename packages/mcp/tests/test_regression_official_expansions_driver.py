import asyncio
from copy import deepcopy
from typing import Any

import pytest

from scripts import regression_official_expansions as driver


@pytest.mark.parametrize(
    ("options", "count", "allow_any", "expected"),
    [
        ([], 2, True, ["Dwarvish", "Elvish"]),
        (["Elvish"], 2, True, ["Elvish", "Dwarvish"]),
        (["Giant"], 1, False, ["Giant"]),
    ],
)
def test_official_regression_fallback_uses_ordinary_language_choices(
    monkeypatch, options, count, allow_any, expected
):
    async def catalog(_server, name, arguments):
        assert name == "character_query"
        assert arguments == {
            "view": "catalog",
            "payload": {"campaign_id": "campaign", "query": "background", "include_context": True},
        }
        return [
            {
                "id": "background",
                "kind": "background",
                "pack_id": "official",
                "application_state": "selection_ready",
                "runtime_context": {
                    "selection_contract": {
                        "status": "ready",
                        "materializer": "dnd5e.character.background.v1",
                        "reviewed_content_hash": "a" * 64,
                    }
                },
                "selection_requirements": {
                    "language_count": count,
                    "language_options": options,
                    "allow_any_language": allow_any,
                },
            }
        ]

    monkeypatch.setattr(driver, "_call", catalog)
    _, selection = asyncio.run(
        driver._catalog_selection(
            None,
            "campaign",
            "background",
            expected_kind="background",
            official_pack_ids={"official"},
        )
    )
    assert selection == {"languages": expected}


class _FakeServer:
    def __init__(self, responses: dict[str, list[Any]]) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[None, dict[str, Any]]:
        self.calls.append((name, arguments))
        return None, {"result": self.responses[name].pop(0)}


@pytest.mark.parametrize("already_applied", [False, True])
def test_finish_artificer_build_consumes_features_and_preparation(monkeypatch, already_applied):
    original = {
        "id": "character-1",
        "revision": 4,
        "sheet": {"content": {"features": []}},
    }
    if already_applied:
        original["sheet"]["content"]["features"] = [{"id": driver._BATTLE_READY}]
    before = deepcopy(original)
    expected = [
        artifact_id
        for artifact_id in driver._ARTIFICER_FEATURE_ORDER
        if not already_applied or artifact_id != driver._BATTLE_READY
    ]
    assert len(driver._ARTIFICER_FEATURE_ORDER) == 9
    selected = []
    applied = []

    async def catalog(_server, campaign, artifact_id, **kwargs):
        assert campaign == "campaign-1"
        choices = (
            {"infusions": list(driver._ARTIFICER_INFUSIONS)}
            if artifact_id.endswith(".feature.infuse-item")
            else {}
        )
        assert kwargs == {
            "expected_kind": "feature",
            "official_pack_ids": {"official"},
            "selection_overrides": choices,
        }
        selected.append(artifact_id)
        return {"id": artifact_id}, choices

    async def apply(_server, character, entry, key, **kwargs):
        assert character["revision"] == 4 + len(applied)
        choices = (
            {"infusions": list(driver._ARTIFICER_INFUSIONS)}
            if entry["id"].endswith(".feature.infuse-item")
            else {}
        )
        assert kwargs == {"ruleset_fingerprint": "fingerprint", "selection": choices}
        assert key == f"apply-official-feature-{entry['id'].rsplit('.', 1)[-1]}"
        applied.append(entry["id"])
        result = deepcopy(character)
        result["revision"] += 1
        result["sheet"]["content"]["features"].append({"id": entry["id"]})
        return result, {"artifact_id": entry["id"]}

    async def prepare(_server, name, arguments):
        assert name == "character_spell_prepare"
        assert arguments == {
            "character_id": "character-1",
            "mode": "replace_all",
            "payload": {"spell_ids": [driver._CURE_WOUNDS], "event": "setup"},
            "expected_revision": 4 + len(expected),
            "idempotency_key": "official-expansion-prepare-spells",
        }
        # The real facade nests the updated sheet under character, not at root.
        return {"character": {"id": "character-1", "revision": 5 + len(expected), "sheet": {}}}

    monkeypatch.setattr(driver, "_catalog_selection", catalog)
    monkeypatch.setattr(driver, "_apply", apply)
    monkeypatch.setattr(driver, "_call", prepare)
    character, receipts = asyncio.run(
        driver._finish_artificer_build(
            None,
            "campaign-1",
            original,
            official_pack_ids={"official"},
            ruleset_fingerprint="fingerprint",
        )
    )
    assert selected == applied == expected
    assert list(receipts) == expected
    assert character["revision"] == 5 + len(expected)
    assert original == before


@pytest.mark.parametrize("failure", ["pending", "wrong_character", "stale_revision", "flat_sheet"])
def test_finish_artificer_build_rejects_unsettled_preparation(monkeypatch, failure):
    character = {
        "id": "character-1",
        "revision": 12,
        "sheet": {
            "content": {
                "features": [{"id": artifact_id} for artifact_id in driver._ARTIFICER_FEATURE_ORDER]
            }
        },
    }

    async def prepare(*_args):
        updated = {"id": "character-1", "revision": 13, "sheet": {}}
        if failure == "pending":
            return {"status": "pending_choice"}
        if failure == "wrong_character":
            updated["id"] = "other-character"
        if failure == "stale_revision":
            updated["revision"] = 12
        return updated if failure == "flat_sheet" else {"character": updated}

    monkeypatch.setattr(driver, "_call", prepare)
    with pytest.raises(RuntimeError, match="preparation did not settle"):
        asyncio.run(
            driver._finish_artificer_build(
                None,
                "campaign-1",
                character,
                official_pack_ids={"official"},
                ruleset_fingerprint="fingerprint",
            )
        )


def _entry() -> dict[str, Any]:
    return {
        "id": "dnd5e.addon.fixture.feat.verified",
        "kind": "feat",
        "pack_id": "dnd5e.addon.fixture",
        "pack_version": "1.0.0",
        "rule_refs": ["fixture:p1"],
        "runtime_context": {
            "selection_contract": {
                "status": "ready",
                "materializer": "dnd5e.character.feat.v1",
                "reviewed_content_hash": "a" * 64,
            },
            "content_hash": "b" * 64,
        },
    }


def _receipt() -> dict[str, Any]:
    entry = _entry()
    return {
        "ruleset_fingerprint": "ruleset-fingerprint",
        "mechanic_id": "dnd5e.character.feat.v1",
        "event": "character.content.apply",
        "artifact_id": entry["id"],
        "character_id": "character-1",
        "pack_id": entry["pack_id"],
        "pack_version": entry["pack_version"],
        "artifact_content_hash": "b" * 64,
        "reviewed_content_hash": "a" * 64,
        "selection": {"ability": "constitution"},
        "rule_refs": entry["rule_refs"],
    }


def test_official_expansion_driver_requires_an_exact_content_receipt() -> None:
    receipt = _receipt()
    applied = {"revision": 2, "sheet": {"name": "Synthetic"}, "rule_receipts": [receipt]}
    server = _FakeServer({"character_content_apply": [applied]})

    result, observed = asyncio.run(
        driver._apply(
            server,
            {"id": "character-1", "revision": 1},
            _entry(),
            "apply-fixture",
            ruleset_fingerprint="ruleset-fingerprint",
            selection={"ability": "constitution"},
        )
    )

    assert result == applied
    assert observed == receipt
    assert server.calls == [
        (
            "character_content_apply",
            {
                "character_id": "character-1",
                "artifact_id": _entry()["id"],
                "selection": {"ability": "constitution"},
                "expected_revision": 1,
                "idempotency_key": "apply-fixture",
            },
        )
    ]

    mismatched = deepcopy(applied)
    mismatched["rule_receipts"][0]["reviewed_content_hash"] = "b" * 64
    with pytest.raises(RuntimeError, match="content receipt mismatch"):
        asyncio.run(
            driver._apply(
                _FakeServer({"character_content_apply": [mismatched]}),
                {"id": "character-1", "revision": 1},
                _entry(),
                "apply-mismatch",
                ruleset_fingerprint="ruleset-fingerprint",
                selection={"ability": "constitution"},
            )
        )


def test_official_expansion_driver_reconciles_restart_state_and_receipts() -> None:
    receipt = _receipt()
    checkpoint = {
        "campaign_id": "campaign-1",
        "campaign_revision": 11,
        "character_id": "character-1",
        "character_revision": 7,
        "character_sheet": {"name": "Synthetic", "constitution": 12},
        "resolution_id": "resolution-1",
        "resolution_total": 17,
        "content_receipts": {receipt["artifact_id"]: receipt},
        "official_addons": {("dnd5e.addon.fixture", "1.0.0")},
    }
    server = _FakeServer(
        {
            "campaign_query": [
                {
                    "revision": 11,
                    "state": {"resolution_log": [{"id": "resolution-1", "result": {"total": 17}}]},
                }
            ],
            "character_query": [{"revision": 7, "sheet": checkpoint["character_sheet"]}],
            "content_pack": [
                [
                    {
                        "addon_id": "dnd5e.addon.fixture",
                        "version": "1.0.0",
                        "built_in_official_expansion": True,
                        "activation": {"enabled": True},
                    }
                ]
            ],
            "campaign_rules": [
                [
                    {
                        "operation": "character.content.apply",
                        "event": "character.content.apply",
                        "mechanic_id": receipt["mechanic_id"],
                        "ruleset_fingerprint": receipt["ruleset_fingerprint"],
                        "mutation_group_id": "mutation-1",
                        "applied": True,
                        "receipt": receipt,
                    }
                ]
            ],
        }
    )

    assert asyncio.run(driver._verify_restart(server, checkpoint)) == 1
    assert [name for name, _ in server.calls] == [
        "campaign_query",
        "character_query",
        "content_pack",
        "campaign_rules",
    ]


def _complete_build_fixture():
    def spell(identifier, name, level, source_type, source_key, **access):
        return {
            "id": identifier,
            "name": name,
            "level": level,
            "grant": {"source_type": source_type, "source_key": source_key},
            "access": access,
        }

    return {
        "content": {
            "features": [{"id": item} for item in driver._REQUIRED_ARTIFICER_FEATURES],
            "spells": [
                spell("cantrip-1", "First", 0, "class", "Artificer", known=True),
                spell("cantrip-2", "Second", 0, "class", "Artificer", known=True),
                spell("prepared", "Prepared", 1, "class", "Artificer", prepared=True),
                spell(
                    "heroism",
                    "Heroism",
                    1,
                    "subclass",
                    "Battle Smith",
                    prepared=True,
                    always_prepared=True,
                ),
                spell(
                    "shield",
                    "Shield",
                    1,
                    "subclass",
                    "Battle Smith",
                    prepared=True,
                    always_prepared=True,
                ),
            ],
        },
        "spellcasting": {"preparation": {"max_prepared": 1, "selected_spell_ids": ["prepared"]}},
    }


def test_complete_build_gate_accepts_explicit_required_state():
    assert driver._build_failures(_complete_build_fixture(), []) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_feature",
        "missing_infusion_choices",
        "feat_cantrip",
        "duplicate_cantrip",
        "unprepared",
        "absent_prepared",
        "ordinary_subclass_spell",
        "subclass_consumes_preparation",
        "unknown_follow_up",
        "unapplied_follow_up",
    ],
)
def test_persisted_incomplete_build_cannot_pass(mutation):
    sheet = _complete_build_fixture()
    follow_ups = []
    spells = sheet["content"]["spells"]
    if mutation == "missing_feature":
        sheet["content"]["features"].pop()
    elif mutation == "missing_infusion_choices":
        sheet["content"]["features"] = [
            {"id": artifact_id} for artifact_id in driver._ARTIFICER_FEATURE_ORDER
        ]
    elif mutation == "feat_cantrip":
        spells[0]["grant"]["source_type"] = "feat"
    elif mutation == "duplicate_cantrip":
        spells[0]["id"] = spells[1]["id"]
    elif mutation == "unprepared":
        spells[2]["access"]["prepared"] = False
    elif mutation == "absent_prepared":
        sheet["spellcasting"]["preparation"]["selected_spell_ids"] = []
    elif mutation == "ordinary_subclass_spell":
        spells[3]["access"]["always_prepared"] = False
    elif mutation == "subclass_consumes_preparation":
        sheet["spellcasting"]["preparation"]["selected_spell_ids"] = ["heroism"]
    elif mutation == "unknown_follow_up":
        follow_ups = [{"spell_choices": {"unknown_choice": 1}}]
    elif mutation == "unapplied_follow_up":
        follow_ups = [{"feature_artifacts": [{"artifact_id": "missing"}]}]
    assert driver._build_failures(sheet, follow_ups)


@pytest.mark.parametrize(
    "field", ["character_id", "pack_version", "artifact_content_hash", "reviewed_content_hash"]
)
def test_content_receipt_identity_mismatch_is_rejected(field):
    receipt = _receipt()
    receipt[field] = "wrong"
    result = {"revision": 2, "sheet": {}, "rule_receipts": [receipt]}
    with pytest.raises(RuntimeError, match="content receipt mismatch"):
        asyncio.run(
            driver._apply(
                _FakeServer({"character_content_apply": [result]}),
                {"id": "character-1", "revision": 1},
                _entry(),
                "identity-mismatch",
                ruleset_fingerprint="ruleset-fingerprint",
                selection={"ability": "constitution"},
            )
        )


def test_catalog_membership_without_selection_contract_is_not_executable(monkeypatch):
    entry = _entry()
    entry["application_state"] = "selection_ready"
    entry["runtime_context"] = {}

    async def catalog(*_args):
        return [entry]

    monkeypatch.setattr(driver, "_call", catalog)
    with pytest.raises(RuntimeError, match="no ready selection contract"):
        asyncio.run(
            driver._catalog_selection(
                None,
                "campaign-1",
                entry["id"],
                expected_kind="feat",
                official_pack_ids={entry["pack_id"]},
            )
        )


@pytest.mark.parametrize(
    "failures,unverified,expected",
    [
        ([], [], True),
        (["missing_required_features"], [], False),
        ([], ["class_starting_equipment"], False),
        ([], None, False),
    ],
)
def test_execute_never_marks_persisted_incomplete_build_passed(
    monkeypatch, tmp_path, failures, unverified, expected
):
    servers = [object(), object()]
    created = []
    closed = []
    report = {"build": {"failures": failures}, "receipts": {}, "persistence": {}}
    if unverified is not None:
        report["build"]["unverified_requirements"] = unverified

    def create(library, home):
        assert library == tmp_path / "library" and home == tmp_path / "home"
        created.append(servers[len(created)])
        return created[-1]

    async def run(server):
        assert server is servers[0]
        return report, {"fixture": "checkpoint"}

    async def restart(server, checkpoint):
        assert server is servers[1] and checkpoint == {"fixture": "checkpoint"}
        return 8

    monkeypatch.setattr(driver, "_create_regression_server", create)
    monkeypatch.setattr(driver, "_run", run)
    monkeypatch.setattr(driver, "_verify_restart", restart)
    monkeypatch.setattr(driver, "close_server", closed.append)
    result = driver._execute(tmp_path / "library", tmp_path / "home")
    assert result["passed"] is expected
    assert result["receipts"]["restart_persisted"] == 8
    assert result["persistence"]["restart_verified"] is True
    assert closed == servers


def test_official_build_retains_known_unverified_requirements():
    assert "class_starting_equipment" in driver._UNVERIFIED_BUILD_REQUIREMENTS
    assert "spellcasting_tool_requirements" in driver._UNVERIFIED_BUILD_REQUIREMENTS
    assert "feature_driven_defender_creation" in driver._UNVERIFIED_BUILD_REQUIREMENTS


@pytest.mark.parametrize("changed_choice", [False, True])
def test_normalized_selection_receipt_matches_record_and_requested_choices(changed_choice):
    entry = _entry()
    entry["kind"] = "class"
    receipt = _receipt()
    requested = {"skills": ["Arcana"], "tools": []}
    recorded = {"skills": ["history" if changed_choice else "arcana"], "tools": []}
    receipt["selection"] = recorded
    result = {
        "revision": 2,
        "sheet": {
            "content": {
                "selections": [
                    {
                        "artifact_id": entry["id"],
                        "kind": "class",
                        "pack_id": entry["pack_id"],
                        "pack_version": entry["pack_version"],
                        "selection": recorded,
                    }
                ]
            }
        },
        "rule_receipts": [receipt],
    }

    async def apply():
        return await driver._apply(
            _FakeServer({"character_content_apply": [result]}),
            {"id": "character-1", "revision": 1},
            entry,
            "normalized",
            ruleset_fingerprint="ruleset-fingerprint",
            selection=requested,
        )

    if changed_choice:
        with pytest.raises(RuntimeError, match="changed requested skills"):
            asyncio.run(apply())
    else:
        assert asyncio.run(apply()) == (result, receipt)
