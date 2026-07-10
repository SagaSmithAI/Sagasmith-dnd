"""Data-driven advancement application for Actor documents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.document_contracts import (
    normalize_activity_document,
    normalize_actor_document,
    normalize_item_document,
)
from sagasmith_dnd.rulesets import get_ruleset


def apply_advancement(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    advancement: dict[str, Any],
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    system = dict(actor.system or {})
    deltas = []
    granted = []
    for step in advancement.get("steps") or []:
        kind = str(step.get("type") or "")
        if kind == "level":
            before = system.get("level", 1)
            system["level"] = int(step.get("value", before))
            deltas.append({"type": kind, "before": before, "after": system["level"]})
        elif kind == "hit_points":
            delta = _apply_hit_points(system, step)
            deltas.append(delta)
        elif kind == "scale_value":
            delta = _apply_scale_value(system, step)
            deltas.append(delta)
        elif kind == "item_grant":
            item_type = str(step.get("item_type") or step.get("item", {}).get("type") or "feat")
            item = documents.create_item(
                campaign_id=campaign_id,
                system_id=actor.system_id,
                actor_id=actor_id,
                item_type=item_type,
                name=str(step.get("name") or step.get("item", {}).get("name") or "Granted Item"),
                source_key=str(step.get("source_key") or step.get("item", {}).get("_id") or ""),
                system=normalize_item_document(
                    item_type,
                    dict(step.get("system") or step.get("item", {}).get("system") or {}),
                ),
                effects=list(step.get("effects") or step.get("item", {}).get("effects") or []),
                flags={"dnd5e": {"advancement": step}},
            )
            granted.append(asdict(item))
            deltas.append({"type": kind, "item_id": item.id, "name": item.name})
        else:
            raise ValueError(f"unsupported advancement step: {kind}")
    updated = documents.update_actor(
        actor_id,
        system=normalize_actor_document(actor.actor_type, system),
    )
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="advancement",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        deltas=deltas,
        narration_hints=[f"{actor.name}'s advancement is applied."],
        flags={"dnd5e": {"advancement": advancement}},
    )
    return {
        "actor": asdict(updated),
        "granted_items": granted,
        "deltas": deltas,
        "messages": [asdict(message)],
    }


def grant_ruleset_feature(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    feature_id: str,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    ruleset = get_ruleset(ruleset_id)
    normalized = _feature_key(feature_id)
    feature = dict(ruleset.get("classFeatures", {}).get(normalized) or {})
    if not feature:
        raise ValueError(f"unknown ruleset feature: {feature_id}")
    item_type = str(feature.get("item_type") or "feat")
    item = documents.create_item(
        campaign_id=campaign_id,
        system_id=actor.system_id,
        actor_id=actor_id,
        item_type=item_type,
        name=str(feature.get("name") or normalized),
        source_key=normalized,
        system=normalize_item_document(item_type, dict(feature.get("system") or {})),
        effects=list(feature.get("effects") or []),
        flags={"dnd5e": {"ruleset_feature": normalized}},
    )
    activities = []
    for template in feature.get("activities") or []:
        activity_type = str(template.get("type") or "utility")
        contract = normalize_activity_document(
            activity_type,
            activation=dict(template.get("activation") or {}),
            consumption=dict(template.get("consumption") or {}),
            duration=dict(template.get("duration") or {}),
            effects=list(template.get("effects") or []),
            range=dict(template.get("range") or {}),
            target=dict(template.get("target") or {}),
            uses=dict(template.get("uses") or {}),
            system=dict(template.get("system") or {}),
            ruleset_id=ruleset["id"],
        )
        activities.append(
            asdict(
                documents.create_activity(
                    item_id=item.id,
                    activity_type=activity_type,
                    name=str(template.get("name") or feature.get("name") or normalized),
                    activation=contract["activation"],
                    consumption=contract["consumption"],
                    duration=contract["duration"],
                    effects=contract["effects"],
                    range=contract["range"],
                    target=contract["target"],
                    uses=contract["uses"],
                    system=contract["system"],
                    flags={
                        "dnd5e": {
                            "ruleset_feature": normalized,
                            "source_key": str(template.get("source_key") or ""),
                        }
                    },
                )
            )
        )
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="advancement",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        item_id=item.id,
        deltas=[
            {
                "type": "ruleset_feature_grant",
                "feature": normalized,
                "item_id": item.id,
                "activity_ids": [activity["id"] for activity in activities],
            }
        ],
        narration_hints=[f"{actor.name} gains {item.name}."],
        flags={"dnd5e": {"ruleset_feature": normalized}},
    )
    return {"item": asdict(item), "activities": activities, "messages": [asdict(message)]}


def grant_ruleset_spell(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    spell_id: str,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    ruleset = get_ruleset(ruleset_id)
    normalized = _feature_key(spell_id)
    spell = dict(ruleset.get("spells", {}).get(normalized) or {})
    if not spell:
        raise ValueError(f"unknown ruleset spell: {spell_id}")
    item = documents.create_item(
        campaign_id=campaign_id,
        system_id=actor.system_id,
        actor_id=actor_id,
        item_type="spell",
        name=str(spell.get("name") or normalized),
        source_key=normalized,
        system=normalize_item_document("spell", dict(spell.get("system") or {})),
        effects=list(spell.get("effects") or []),
        flags={"dnd5e": {"ruleset_spell": normalized}},
    )
    activities = _create_template_activities(
        documents,
        item_id=item.id,
        templates=list(spell.get("activities") or []),
        ruleset_id=ruleset["id"],
        flag_key="ruleset_spell",
        flag_value=normalized,
        fallback_name=item.name,
    )
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="advancement",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        item_id=item.id,
        deltas=[
            {
                "type": "ruleset_spell_grant",
                "spell": normalized,
                "item_id": item.id,
                "activity_ids": [activity["id"] for activity in activities],
            }
        ],
        narration_hints=[f"{actor.name} learns {item.name}."],
        flags={"dnd5e": {"ruleset_spell": normalized}},
    )
    return {"item": asdict(item), "activities": activities, "messages": [asdict(message)]}


def grant_ruleset_class(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    class_id: str,
    level: int,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    ruleset = get_ruleset(ruleset_id)
    normalized = _feature_key(class_id)
    template = dict(ruleset.get("classes", {}).get(normalized) or {})
    if not template:
        raise ValueError(f"unknown ruleset class: {class_id}")
    if level < 1 or level > 20:
        raise ValueError("class level must be between 1 and 20")
    system = dict(actor.system or {})
    class_levels = dict(system.get("class_levels") or {})
    previous_level = int(class_levels.get(normalized, 0) or 0)
    class_levels[normalized] = level
    system["class_levels"] = class_levels
    classes = dict(system.get("classes") or {})
    classes[normalized] = {
        **dict(classes.get(normalized) or {}),
        "levels": level,
        "hit_die": template.get("hit_die") or "",
    }
    system["classes"] = classes
    for ability in template.get("save_proficiencies") or []:
        abilities = system.setdefault("abilities", {})
        entry = dict(abilities.get(ability) or {"value": 10})
        entry["proficient"] = 1
        abilities[ability] = entry
    actor = documents.update_actor(
        actor_id, system=normalize_actor_document(actor.actor_type, system)
    )
    class_item = _ensure_class_item(documents, actor=actor, template=template, class_id=normalized)
    granted, unresolved = _grant_progression_features(
        documents,
        campaign_id=campaign_id,
        actor_id=actor_id,
        ruleset=ruleset,
        grants=list(template.get("feature_grants") or []),
        level=level,
    )
    return {
        "actor": asdict(actor),
        "class_item": asdict(class_item),
        "class_id": normalized,
        "previous_level": previous_level,
        "level": level,
        "granted_features": granted,
        "unresolved_feature_grants": unresolved,
    }


def grant_ruleset_subclass(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    subclass_id: str,
    level: int,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    ruleset = get_ruleset(ruleset_id)
    normalized = _feature_key(subclass_id)
    template = dict(ruleset.get("subclasses", {}).get(normalized) or {})
    if not template:
        raise ValueError(f"unknown ruleset subclass: {subclass_id}")
    class_id = str(template.get("class_key") or "")
    if int(dict(actor.system or {}).get("class_levels", {}).get(class_id, 0) or 0) < level:
        raise ValueError(f"{actor.name} does not have {class_id} level {level}")
    system = dict(actor.system or {})
    subclasses = dict(system.get("subclasses") or {})
    subclasses[class_id] = normalized
    actor = documents.update_actor(
        actor_id, system=normalize_actor_document(actor.actor_type, system)
    )
    item = documents.create_item(
        campaign_id=campaign_id,
        system_id=actor.system_id,
        actor_id=actor_id,
        item_type="feat",
        name=str(template.get("name") or normalized),
        source_key=normalized,
        system=normalize_item_document("feat", dict(template.get("system") or {})),
        flags={"dnd5e": {"ruleset_subclass": normalized, "class_id": class_id}},
    )
    granted, unresolved = _grant_progression_features(
        documents,
        campaign_id=campaign_id,
        actor_id=actor_id,
        ruleset=ruleset,
        grants=list(template.get("feature_grants") or []),
        level=level,
    )
    return {
        "actor": asdict(actor),
        "subclass_item": asdict(item),
        "subclass_id": normalized,
        "class_id": class_id,
        "granted_features": granted,
        "unresolved_feature_grants": unresolved,
    }


def create_ruleset_monster_actor(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    monster_id: str,
    name: str | None = None,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    ruleset = get_ruleset(ruleset_id)
    normalized = _feature_key(monster_id)
    monster = dict(ruleset.get("monsters", {}).get(normalized) or {})
    if not monster:
        raise ValueError(f"unknown ruleset monster: {monster_id}")
    actor = documents.create_actor(
        campaign_id=campaign_id,
        system_id="dnd5e",
        actor_type="npc",
        name=name or str(monster.get("name") or normalized),
        img=str(monster.get("img") or ""),
        system=normalize_actor_document("npc", dict(monster.get("system") or {})),
        prototype_token=dict(monster.get("prototype_token") or {}),
        flags={"dnd5e": {"ruleset_monster": normalized}},
    )
    items = []
    for template in monster.get("items") or []:
        if not isinstance(template, dict):
            continue
        item_type = str(template.get("type") or "weapon")
        item = documents.create_item(
            campaign_id=campaign_id,
            system_id=actor.system_id,
            actor_id=actor.id,
            item_type=item_type,
            name=str(template.get("name") or "Monster Action"),
            source_key=str(template.get("source_key") or ""),
            system=normalize_item_document(item_type, dict(template.get("system") or {})),
            effects=list(template.get("effects") or []),
            flags={"dnd5e": {"ruleset_monster": normalized}},
        )
        activities = _create_template_activities(
            documents,
            item_id=item.id,
            templates=list(template.get("activities") or []),
            ruleset_id=ruleset["id"],
            flag_key="ruleset_monster",
            flag_value=normalized,
            fallback_name=item.name,
        )
        item_data = asdict(item)
        item_data["activities"] = activities
        items.append(item_data)
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="advancement",
        speaker={"system": "runtime"},
        actor_id=actor.id,
        deltas=[
            {
                "type": "ruleset_monster_create",
                "monster": normalized,
                "actor_id": actor.id,
                "item_ids": [item["id"] for item in items],
            }
        ],
        narration_hints=[f"{actor.name} is added to the encounter roster."],
        flags={"dnd5e": {"ruleset_monster": normalized}},
    )
    return {"actor": asdict(actor), "items": items, "messages": [asdict(message)]}


def _create_template_activities(
    documents: FoundryDocumentService,
    *,
    item_id: str,
    templates: list[Any],
    ruleset_id: str,
    flag_key: str,
    flag_value: str,
    fallback_name: str,
) -> list[dict[str, Any]]:
    activities = []
    for template in templates:
        if not isinstance(template, dict):
            continue
        activity_type = str(template.get("type") or "utility")
        contract = normalize_activity_document(
            activity_type,
            activation=dict(template.get("activation") or {}),
            consumption=dict(template.get("consumption") or {}),
            duration=dict(template.get("duration") or {}),
            effects=list(template.get("effects") or []),
            range=dict(template.get("range") or {}),
            target=dict(template.get("target") or {}),
            uses=dict(template.get("uses") or {}),
            system=dict(template.get("system") or {}),
            ruleset_id=ruleset_id,
        )
        activities.append(
            asdict(
                documents.create_activity(
                    item_id=item_id,
                    activity_type=activity_type,
                    name=str(template.get("name") or fallback_name),
                    activation=contract["activation"],
                    consumption=contract["consumption"],
                    duration=contract["duration"],
                    effects=contract["effects"],
                    range=contract["range"],
                    target=contract["target"],
                    uses=contract["uses"],
                    system=contract["system"],
                    flags={
                        "dnd5e": {
                            flag_key: flag_value,
                            "source_key": str(template.get("source_key") or ""),
                        }
                    },
                )
            )
        )
    return activities


def _ensure_class_item(
    documents: FoundryDocumentService, *, actor, template: dict[str, Any], class_id: str
):
    for item in documents.list_items(actor.campaign_id, actor_id=actor.id, item_type="class"):
        if item.source_key == class_id:
            return item
    return documents.create_item(
        campaign_id=actor.campaign_id,
        system_id=actor.system_id,
        actor_id=actor.id,
        item_type="class",
        name=str(template.get("name") or class_id),
        source_key=class_id,
        system=normalize_item_document("class", dict(template.get("system") or {})),
        flags={"dnd5e": {"ruleset_class": class_id}},
    )


def _grant_progression_features(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    ruleset: dict[str, Any],
    grants: list[dict[str, Any]],
    level: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing = {
        str((item.flags.get("dnd5e") or {}).get("ruleset_feature") or "")
        for item in documents.list_items(campaign_id, actor_id=actor_id)
    }
    index = dict(ruleset.get("featureSourceIndex") or {})
    granted = []
    unresolved = []
    for grant in grants:
        if int(grant.get("level", 1) or 1) > level:
            continue
        foundry_id = str(grant.get("foundry_id") or "")
        feature_id = str(index.get(foundry_id) or "")
        if not feature_id:
            unresolved.append({"level": grant.get("level", 1), "foundry_id": foundry_id})
            continue
        if feature_id in existing:
            continue
        granted.append(
            grant_ruleset_feature(
                documents,
                campaign_id=campaign_id,
                actor_id=actor_id,
                feature_id=feature_id,
                ruleset_id=ruleset["id"],
            )
        )
        existing.add(feature_id)
    return granted, unresolved


def _feature_key(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _apply_hit_points(system: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    attributes = system.setdefault("attributes", {})
    hp = attributes.setdefault("hp", {"value": 1, "max": 1})
    before = dict(hp)
    increase = int(step.get("increase", 0) or 0)
    hp["max"] = int(hp.get("max", 0) or 0) + increase
    if step.get("heal", True):
        hp["value"] = int(hp.get("value", 0) or 0) + increase
    return {"type": "hit_points", "before": before, "after": dict(hp)}


def _apply_scale_value(system: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    scale = system.setdefault("scale", {})
    namespace = str(step.get("namespace") or "class")
    values = scale.setdefault(namespace, {})
    key = str(step.get("key") or "")
    if not key:
        raise ValueError("scale_value step requires key")
    before = values.get(key)
    values[key] = step.get("value")
    return {
        "type": "scale_value",
        "namespace": namespace,
        "key": key,
        "before": before,
        "after": values[key],
    }
