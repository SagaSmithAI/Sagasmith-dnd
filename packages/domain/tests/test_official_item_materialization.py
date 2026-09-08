from __future__ import annotations

import sagasmith_dnd.official_item_materialization as materialization
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    attune_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
    use_official_item_action,
)
from sagasmith_dnd.combat_engine import (
    apply_official_item_effect_to_encounter,
    end_turn,
    preflight_attack,
    resolve_attack_damage,
    roll_attack_action,
)


def _artifact(artifact_id: str) -> dict:
    return {
        "id": artifact_id,
        "kind": "item",
        "card": {"description": f"reviewed source for {artifact_id}"},
    }


def test_reviewed_magic_item_profiles_are_explicit_and_schema_valid(monkeypatch) -> None:
    for artifact_id, expected_hash in materialization._REVIEWED_ITEM_HASHES.items():
        monkeypatch.setattr(
            materialization,
            "content_fingerprint",
            lambda _artifact, h=expected_hash: h,
        )
        artifact = _artifact(artifact_id)
        profile = materialization.official_item_profile(
            materialization.EBERRON_ITEM_PACK_ID,
            artifact,
        )
        assert profile is not None
        if artifact_id == materialization.ARMBLADE_ID:
            base = {
                "name": "Longsword",
                "kind": "weapon",
                "mechanics": {
                    "category": "martial",
                    "attack_type": "melee",
                    "attack_ability": "strength",
                    "damage_formula": "1d8",
                    "damage_type": "slashing",
                    "properties": ["versatile"],
                    "proficient": False,
                },
            }
            template = materialization.materialize_official_item_template(
                materialization.EBERRON_ITEM_PACK_ID,
                artifact,
                base_weapon_template=base,
                pack_version="1.0.8",
            )
            assert template["mechanics"]["magical"] is True
            assert template["mechanics"]["damage_formula"] == "1d8"
            assert template["attunement"] == "required"
            assert template["source_key"] == (
                materialization.EBERRON_ITEM_PACK_ID
                + "@1.0.8:"
                + materialization.ARMBLADE_ID
            )
        else:
            template = materialization.materialize_official_item_template(
                materialization.EBERRON_ITEM_PACK_ID,
                artifact,
                pack_version="1.0.8",
            )
        assert template is not None
        assert template["source_key"] == (
            materialization.EBERRON_ITEM_PACK_ID
            + "@1.0.8:"
            + artifact_id
        )
        sheet, item_id = add_inventory_item(default_character_sheet(), template)
        item = next(item for item in sheet["inventory"]["items"] if item["id"] == item_id)
        assert item["kind"] == "weapon"
        assert item["mechanics"]["magical"] is True


def test_armblade_does_not_invent_a_base_weapon(monkeypatch) -> None:
    assert materialization.is_bound_official_item_id(
        materialization.EBERRON_ITEM_PACK_ID, materialization.ARMBLADE_ID
    )
    assert not materialization.is_bound_official_item_id(
        materialization.EBERRON_ITEM_PACK_ID, "item.unknown"
    )
    monkeypatch.setattr(
        materialization,
        "content_fingerprint",
        lambda _artifact: materialization._REVIEWED_ITEM_HASHES[materialization.ARMBLADE_ID],
    )
    artifact = _artifact(materialization.ARMBLADE_ID)
    assert materialization.materialize_official_item_template(
        materialization.EBERRON_ITEM_PACK_ID,
        artifact,
    ) is None

    two_handed = {
        "name": "Greatsword",
        "kind": "weapon",
        "mechanics": {
            "category": "martial",
            "attack_type": "melee",
            "attack_ability": "strength",
            "damage_formula": "2d6",
            "damage_type": "slashing",
            "properties": ["heavy", "two-handed"],
        },
    }
    # The materializer intentionally leaves one-handed validation to the MCP
    # boundary, where the selected core artifact is available and reviewed.
    assert materialization.materialize_official_item_template(
        materialization.EBERRON_ITEM_PACK_ID,
        artifact,
        base_weapon_template=two_handed,
    ) is not None


def test_bound_profile_requires_the_exact_reviewed_fingerprint(monkeypatch) -> None:
    artifact = _artifact(materialization.ARCANE_PROPULSION_ARM_ID)
    monkeypatch.setattr(materialization, "content_fingerprint", lambda _artifact: "tampered")
    assert materialization.is_bound_official_item_id(
        materialization.EBERRON_ITEM_PACK_ID, artifact["id"]
    )
    assert materialization.official_item_profile(
        materialization.EBERRON_ITEM_PACK_ID, artifact
    ) is None
    assert materialization.materialize_official_item_template(
        materialization.EBERRON_ITEM_PACK_ID, artifact
    ) is None


def _weapon_actor(item_template: dict, *, species: str = "") -> tuple[dict, str]:
    sheet = default_character_sheet()
    sheet["progression"]["species"] = species
    sheet, item_id = add_inventory_item(sheet, item_template)
    sheet = equip_inventory_item(sheet, item_id, "main_hand")
    sheet = attune_inventory_item(sheet, item_id)
    source_key = str(item_template.get("source_key") or "")
    if "@" in source_key and ":" in source_key:
        pack_version, artifact_id = source_key.rsplit(":", 1)
        pack_id, version = pack_version.split("@", 1)
        reviewed_hash = materialization.reviewed_official_item_hash(artifact_id)
        if reviewed_hash is not None:
            sheet["content"]["selections"].append(
                {
                    "artifact_id": artifact_id,
                    "kind": "item",
                    "pack_id": pack_id,
                    "pack_version": version,
                    "selection": {
                        "inventory_item_id": item_id,
                        "artifact_content_hash": reviewed_hash,
                        "reviewed_content_hash": reviewed_hash,
                        "materialized_item_hash": materialization.materialized_item_binding_hash(
                            next(
                                item
                                for item in sheet["inventory"]["items"]
                                if item["id"] == item_id
                            )
                        ),
                    },
                }
            )
    return {
        "id": "attacker",
        "name": "attacker",
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
    }, item_id


def test_official_item_actions_are_stateful_and_armblade_is_not_attackable_retracted(
    monkeypatch,
) -> None:
    artifact = _artifact(materialization.ARMBLADE_ID)
    monkeypatch.setattr(
        materialization,
        "content_fingerprint",
        lambda _artifact: materialization._REVIEWED_ITEM_HASHES[materialization.ARMBLADE_ID],
    )
    template = materialization.materialize_official_item_template(
        materialization.EBERRON_ITEM_PACK_ID,
        artifact,
        pack_version="1.0.8",
        base_weapon_template={
            "name": "Longsword",
            "kind": "weapon",
            "mechanics": {
                "category": "martial",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "properties": ["versatile"],
                "proficient": True,
            },
        },
    )
    actor, item_id = _weapon_actor(template, species="Warforged")
    assert actor["derived"]["inventory"]["weapon_attacks"]
    retracted_sheet, transition = use_official_item_action(actor["sheet"], item_id, "toggle")
    assert transition["state"] == "retracted"
    assert not derive_character_sheet(retracted_sheet)["inventory"]["weapon_attacks"]
    extended_sheet, transition = use_official_item_action(retracted_sheet, item_id, "toggle")
    assert transition["state"] == "extended"
    assert derive_character_sheet(extended_sheet)["inventory"]["weapon_attacks"]


def test_dyrrn_adds_aberration_disadvantage_and_structured_natural_twenty_stun(monkeypatch) -> None:
    artifact = _artifact(materialization.DYRRN_TENTACLE_WHIP_ID)
    monkeypatch.setattr(
        materialization,
        "content_fingerprint",
        lambda _artifact: materialization._REVIEWED_ITEM_HASHES[
            materialization.DYRRN_TENTACLE_WHIP_ID
        ],
    )
    template = materialization.materialize_official_item_template(
        materialization.EBERRON_ITEM_PACK_ID,
        artifact,
        pack_version="1.0.8",
    )
    attacker, item_id = _weapon_actor(template)
    target_sheet = default_character_sheet()
    target_sheet["progression"]["species"] = "Aberration"
    target_sheet["combat"]["hp"] = {"value": 200, "max": 200, "temp": 0}
    target = {
        "id": "target",
        "name": "target",
        "sheet": target_sheet,
        "derived": derive_character_sheet(target_sheet),
    }
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": item_id, "attack_mode": "melee"},
        require_attack_action=False,
    )
    assert plan["disadvantage"] is True
    assert "official_item:dyrrn_tentacle_whip:aberration" in plan["disadvantage_sources"]
    attack = roll_attack_action(
        plan=plan,
        rng=type("Rng", (), {"randint": lambda _s, _a, _b: 20})(),
    )
    assert attack["natural"] == 20
    _, updated_target, result = resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack=attack,
    )
    assert result["official_item_effect"]["kind"] == "dyrrn_natural_20_stun"
    encounter = {
        "active": True,
        "ruleset": "2014",
        "combatants": [
            {"actor_id": "attacker", "conditions": []},
            {"actor_id": "target", "conditions": []},
        ],
        "ongoing_effects": [],
    }
    committed = apply_official_item_effect_to_encounter(
        encounter,
        result,
        attacker_id="attacker",
        target_id="target",
    )
    assert "stunned" in committed["encounter"]["combatants"][1]["conditions"]
    assert committed["effect"]["expires_on"] == "target_turn_end"
    current_target_encounter = {
        "active": True,
        "ruleset": "2014",
        "round": 1,
        "turn_index": 0,
        "combatants": [
            {"actor_id": "target", "conditions": [], "turns_completed": 1},
            {"actor_id": "attacker", "conditions": [], "turns_completed": 1},
        ],
        "ongoing_effects": [],
    }
    current_target_commit = apply_official_item_effect_to_encounter(
        current_target_encounter,
        result,
        attacker_id="attacker",
        target_id="target",
    )
    after_current_turn = end_turn(current_target_commit["encounter"], actor_id_value="target")
    assert current_target_commit["effect"]["active"] is True
    assert after_current_turn["ongoing_effects"][0]["active"] is True
    assert updated_target["sheet"]["conditions"] == []


def test_official_attack_effects_require_attunement_and_applied_selection(monkeypatch) -> None:
    artifact = _artifact(materialization.DYRRN_TENTACLE_WHIP_ID)
    monkeypatch.setattr(
        materialization,
        "content_fingerprint",
        lambda _artifact: materialization._REVIEWED_ITEM_HASHES[
            materialization.DYRRN_TENTACLE_WHIP_ID
        ],
    )
    template = materialization.materialize_official_item_template(
        materialization.EBERRON_ITEM_PACK_ID,
        artifact,
        pack_version="1.0.8",
    )
    sheet = default_character_sheet()
    sheet, item_id = add_inventory_item(sheet, template)
    sheet = equip_inventory_item(sheet, item_id, "main_hand")
    attacker = {
        "id": "attacker",
        "name": "attacker",
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
    }
    target_sheet = default_character_sheet()
    target_sheet["progression"]["species"] = "Aberration"
    target = {
        "id": "target",
        "name": "target",
        "sheet": target_sheet,
        "derived": derive_character_sheet(target_sheet),
    }
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": item_id, "attack_mode": "melee"},
        require_attack_action=False,
    )
    assert plan["disadvantage"] is False
    assert plan["official_item"] == {}

    # A receipt for the original mechanics must not authorize a later generic
    # inventory patch that changes the reviewed weapon's magic payload.
    from sagasmith_dnd.content_validation import content_fingerprint as real_content_fingerprint

    monkeypatch.setattr(materialization, "content_fingerprint", real_content_fingerprint)
    item = next(item for item in sheet["inventory"]["items"] if item["id"] == item_id)
    source_key = str(item["source_key"])
    artifact_id = source_key.rsplit(":", 1)[-1]
    pack_id, versioned_artifact = source_key.split("@", 1)
    pack_version = versioned_artifact.split(":", 1)[0]
    sheet["content"]["selections"].append(
        {
            "artifact_id": artifact_id,
            "kind": "item",
            "name": item["name"],
            "pack_id": pack_id,
            "pack_version": pack_version,
            "rule_refs": [],
            "mechanic_refs": [],
            "selection": {
                "inventory_item_id": item_id,
                "artifact_content_hash": materialization.reviewed_official_item_hash(artifact_id),
                "reviewed_content_hash": materialization.reviewed_official_item_hash(artifact_id),
                "materialized_item_hash": materialization.materialized_item_binding_hash(item),
            },
        }
    )
    # A generic inventory mutation must not turn a source-required official
    # weapon into an unattuned magic weapon.  The binding receipt is still
    # valid because runtime attunement is intentionally excluded from its
    # mechanics hash.
    item = next(item for item in sheet["inventory"]["items"] if item["id"] == item_id)
    item["attunement"] = "none"
    attacker["derived"] = derive_character_sheet(sheet)
    suppressed = preflight_attack(
        attacker,
        target,
        action={"weapon_id": item_id, "attack_mode": "melee"},
        require_attack_action=False,
    )
    assert suppressed["damage_expression"] == "1d4"
    assert suppressed["additional_damage"] == []
    assert suppressed["official_item"] == {}
    item["attunement"] = "required"
    attacker["derived"] = derive_character_sheet(sheet)
    item["mechanics"]["magic_bonus"] = 99
    attacker["derived"] = derive_character_sheet(sheet)
    tampered = preflight_attack(
        attacker,
        target,
        action={"weapon_id": item_id, "attack_mode": "melee"},
        require_attack_action=False,
    )
    assert tampered["official_item"] == {}
    assert tampered["additional_damage"] == []

    item = next(item for item in sheet["inventory"]["items"] if item["id"] == item_id)
    item["attunement"] = "attuned"
    attacker["derived"] = derive_character_sheet(sheet)
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": item_id, "attack_mode": "melee"},
        require_attack_action=False,
    )
    assert plan["disadvantage"] is False
    assert plan["official_item"] == {}


def test_arcane_propulsion_arm_throw_emits_return_and_remove_state_is_not_attackable(
    monkeypatch,
) -> None:
    artifact = _artifact(materialization.ARCANE_PROPULSION_ARM_ID)
    monkeypatch.setattr(
        materialization,
        "content_fingerprint",
        lambda _artifact: materialization._REVIEWED_ITEM_HASHES[
            materialization.ARCANE_PROPULSION_ARM_ID
        ],
    )
    template = materialization.materialize_official_item_template(
        materialization.EBERRON_ITEM_PACK_ID,
        artifact,
        pack_version="1.0.8",
    )
    actor, item_id = _weapon_actor(template)
    target_sheet = default_character_sheet()
    target = {
        "id": "target",
        "sheet": target_sheet,
        "derived": derive_character_sheet(target_sheet),
    }
    plan = preflight_attack(
        actor,
        target,
        action={"weapon_id": item_id, "attack_mode": "ranged"},
        require_attack_action=False,
    )
    attack = roll_attack_action(
        plan=plan,
        rng=type("Rng", (), {"randint": lambda _s, _a, _b: 10})(),
    )
    _, _, result = resolve_attack_damage(
        actor,
        target,
        plan=plan,
        attack=attack,
    )
    assert result["official_item_effect"]["kind"] == "arcane_propulsion_arm_return"
    _, _, miss_result = resolve_attack_damage(
        actor,
        target,
        plan=plan,
        attack={**attack, "hit": False, "critical": False},
    )
    assert miss_result["official_item_effect"]["kind"] == "arcane_propulsion_arm_return"
    detached, transition = use_official_item_action(actor["sheet"], item_id, "remove")
    assert transition["state"] == "detached"
    assert not derive_character_sheet(detached)["inventory"]["weapon_attacks"]
