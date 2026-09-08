from __future__ import annotations

import sagasmith_dnd.official_item_materialization as materialization
from sagasmith_dnd.character_schema import add_inventory_item, default_character_sheet


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
            )
            assert template["mechanics"]["magical"] is True
            assert template["mechanics"]["damage_formula"] == "1d8"
            assert template["attunement"] == "required"
            assert template["source_key"] == (
                materialization.EBERRON_ITEM_PACK_ID + ":" + materialization.ARMBLADE_ID
            )
        else:
            template = materialization.materialize_official_item_template(
                materialization.EBERRON_ITEM_PACK_ID,
                artifact,
            )
        assert template is not None
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
