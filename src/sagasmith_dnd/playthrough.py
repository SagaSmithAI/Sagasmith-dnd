"""Validated, snapshot-managed full-campaign playthrough manifests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SCHEMA_VERSION = 1
PLAYTHROUGH_STATUSES = {
    "lobby",
    "ready",
    "in_progress",
    "completed",
    "blocked",
}
ACTOR_STATUSES = {"active", "dead", "missing", "departed", "reserve"}
QUEST_STATUSES = {"unavailable", "available", "active", "completed", "failed", "closed"}
CLUE_STATUSES = {"hidden", "available", "discovered", "resolved", "lost"}
ENDING_STATUSES = {"pending", "eligible", "completed", "failed"}
CHECK_KINDS = {
    "manifest_value",
    "campaign_state_value",
    "actor_value",
    "memory_fact",
}
CHECK_OPERATORS = {"equals", "not_equals", "in", "at_least", "at_most", "truthy"}


def new_playthrough_manifest(
    *,
    run_id: str,
    campaign_line_id: str,
    module_ids: list[str],
    recommended_party_minimum: int | None,
    recommended_party_maximum: int | None,
    selected_party_size: int | None,
    source_refs: list[dict[str, Any]],
    review_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create the complete empty shape used before party construction."""

    return validate_playthrough_manifest(
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "campaign_line_id": campaign_line_id,
            "module_ids": list(module_ids),
            "status": "lobby",
            "source_refs": source_refs,
            "current": {
                "module_id": "",
                "chapter_id": "",
                "chapter_title": "",
                "scene_id": "",
                "scene_title": "",
                "objective": "",
            },
            "traversal": {
                "reachable_scene_ids": [],
                "visited_scene_ids": [],
                "excluded_scenes": [],
                "branch_decisions": [],
            },
            "party": {
                "recommended_minimum": recommended_party_minimum,
                "recommended_maximum": recommended_party_maximum,
                "selected_size": selected_party_size,
                "use_pregenerated_first": True,
                "members": [],
                "replacements": [],
            },
            "npcs": [],
            "quests": [],
            "clues": [],
            "world_state": {},
            "snapshot_dag": {
                "active_branch_id": "",
                "head_snapshot_id": "",
                "nodes": [],
            },
            "random_stream": {
                "algorithm": "",
                "seed_fingerprint": "",
                "position": 0,
            },
            "ending": {
                "status": "pending",
                "conditions": [],
                "achieved_condition_id": "",
                "verification": [],
            },
            "review_blocks": list(review_blocks or []),
        }
    )


def validate_playthrough_manifest(value: Any) -> dict[str, Any]:
    manifest = _object(value, "playthrough_manifest")
    _only(
        manifest,
        "playthrough_manifest",
        {
            "schema_version",
            "run_id",
            "campaign_line_id",
            "module_ids",
            "status",
            "source_refs",
            "current",
            "traversal",
            "party",
            "npcs",
            "quests",
            "clues",
            "world_state",
            "snapshot_dag",
            "random_stream",
            "ending",
            "review_blocks",
        },
    )
    schema_version = _integer(manifest.get("schema_version"), "schema_version", minimum=1)
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported playthrough manifest schema {schema_version}")
    module_ids = _unique_strings(manifest.get("module_ids"), "module_ids")
    if not module_ids:
        raise ValueError("playthrough_manifest.module_ids must not be empty")
    status = _choice(manifest.get("status"), "status", PLAYTHROUGH_STATUSES)
    current = _validate_current(manifest.get("current"))
    if current["module_id"] and current["module_id"] not in module_ids:
        raise ValueError("playthrough_manifest.current.module_id is not in module_ids")
    traversal = _validate_traversal(manifest.get("traversal"))
    party = _validate_party(manifest.get("party"))
    npcs = [_validate_npc(item, index) for index, item in enumerate(_list(manifest.get("npcs")))]
    quests = [
        _validate_quest(item, index) for index, item in enumerate(_list(manifest.get("quests")))
    ]
    clues = [
        _validate_clue(item, index) for index, item in enumerate(_list(manifest.get("clues")))
    ]
    _require_unique(npcs, "actor_id", "npcs")
    _require_unique(quests, "id", "quests")
    _require_unique(clues, "id", "clues")
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _required_text(manifest.get("run_id"), "run_id"),
        "campaign_line_id": _required_text(
            manifest.get("campaign_line_id"), "campaign_line_id"
        ),
        "module_ids": module_ids,
        "status": status,
        "source_refs": [
            validate_source_ref(item, field=f"source_refs[{index}]")
            for index, item in enumerate(_list(manifest.get("source_refs")))
        ],
        "current": current,
        "traversal": traversal,
        "party": party,
        "npcs": npcs,
        "quests": quests,
        "clues": clues,
        "world_state": _json_object(manifest.get("world_state"), "world_state"),
        "snapshot_dag": _validate_snapshot_dag(manifest.get("snapshot_dag")),
        "random_stream": _validate_random_projection(manifest.get("random_stream")),
        "ending": _validate_ending(manifest.get("ending")),
        "review_blocks": [
            _json_object(item, f"review_blocks[{index}]")
            for index, item in enumerate(_list(manifest.get("review_blocks")))
        ],
    }
    if status in {"ready", "in_progress", "completed"}:
        if normalized["review_blocks"]:
            raise ValueError("playthrough cannot leave lobby while review blocks remain")
        selected_size = party["selected_size"]
        if selected_size is None:
            raise ValueError("playthrough cannot leave lobby without a selected party size")
        if len(party["members"]) != selected_size:
            raise ValueError(
                "playthrough cannot leave lobby until party members match selected_size"
            )
    if status in {"in_progress", "completed"} and not current["scene_id"]:
        raise ValueError("active playthrough requires a current scene")
    return normalized


def validate_source_ref(value: Any, *, field: str = "source_ref") -> dict[str, Any]:
    ref = _object(value, field)
    _only(
        ref,
        field,
        {
            "purpose",
            "asset_path",
            "asset_sha256",
            "page_start",
            "page_end",
            "heading_path",
            "chunk_content_sha256",
            "module_id",
            "scene_id",
            "chunk_id",
            "excerpt",
        },
    )
    page_start = _integer(ref.get("page_start"), f"{field}.page_start", minimum=1)
    page_end = _integer(ref.get("page_end"), f"{field}.page_end", minimum=page_start)
    asset_sha = _required_text(ref.get("asset_sha256"), f"{field}.asset_sha256").casefold()
    chunk_sha = _required_text(
        ref.get("chunk_content_sha256"), f"{field}.chunk_content_sha256"
    ).casefold()
    if not _is_sha256(asset_sha) or not _is_sha256(chunk_sha):
        raise ValueError(f"{field} SHA-256 fields must contain 64 lowercase hex characters")
    return {
        "purpose": _required_text(ref.get("purpose"), f"{field}.purpose"),
        "asset_path": _required_text(ref.get("asset_path"), f"{field}.asset_path"),
        "asset_sha256": asset_sha,
        "page_start": page_start,
        "page_end": page_end,
        "heading_path": _unique_strings(ref.get("heading_path"), f"{field}.heading_path"),
        "chunk_content_sha256": chunk_sha,
        "module_id": _text(ref.get("module_id")),
        "scene_id": _text(ref.get("scene_id")),
        "chunk_id": _text(ref.get("chunk_id")),
        "excerpt": _text(ref.get("excerpt")),
    }


def _validate_current(value: Any) -> dict[str, str]:
    current = _object(value, "current")
    fields = {
        "module_id",
        "chapter_id",
        "chapter_title",
        "scene_id",
        "scene_title",
        "objective",
    }
    _only(current, "current", fields)
    return {field: _text(current.get(field)) for field in fields}


def _validate_traversal(value: Any) -> dict[str, Any]:
    traversal = _object(value, "traversal")
    _only(
        traversal,
        "traversal",
        {
            "reachable_scene_ids",
            "visited_scene_ids",
            "excluded_scenes",
            "branch_decisions",
        },
    )
    reachable = _unique_strings(traversal.get("reachable_scene_ids"), "reachable_scene_ids")
    visited = _unique_strings(traversal.get("visited_scene_ids"), "visited_scene_ids")
    excluded = [
        _validate_excluded_scene(item, index)
        for index, item in enumerate(_list(traversal.get("excluded_scenes")))
    ]
    decisions = [
        _validate_branch_decision(item, index)
        for index, item in enumerate(_list(traversal.get("branch_decisions")))
    ]
    _require_unique(excluded, "scene_id", "excluded_scenes")
    _require_unique(decisions, "id", "branch_decisions")
    if set(visited) - set(reachable):
        raise ValueError("visited_scene_ids must be a subset of reachable_scene_ids")
    if set(item["scene_id"] for item in excluded) & set(visited):
        raise ValueError("a scene cannot be both visited and excluded")
    return {
        "reachable_scene_ids": reachable,
        "visited_scene_ids": visited,
        "excluded_scenes": excluded,
        "branch_decisions": decisions,
    }


def _validate_excluded_scene(value: Any, index: int) -> dict[str, Any]:
    field = f"excluded_scenes[{index}]"
    item = _object(value, field)
    _only(item, field, {"scene_id", "reason", "source_ref"})
    return {
        "scene_id": _required_text(item.get("scene_id"), f"{field}.scene_id"),
        "reason": _required_text(item.get("reason"), f"{field}.reason"),
        "source_ref": (
            validate_source_ref(item["source_ref"], field=f"{field}.source_ref")
            if item.get("source_ref")
            else None
        ),
    }


def _validate_branch_decision(value: Any, index: int) -> dict[str, Any]:
    field = f"branch_decisions[{index}]"
    item = _object(value, field)
    _only(
        item,
        field,
        {"id", "parent_snapshot_id", "selected_branch_id", "excluded_branch_ids", "reason"},
    )
    return {
        "id": _required_text(item.get("id"), f"{field}.id"),
        "parent_snapshot_id": _required_text(
            item.get("parent_snapshot_id"), f"{field}.parent_snapshot_id"
        ),
        "selected_branch_id": _required_text(
            item.get("selected_branch_id"), f"{field}.selected_branch_id"
        ),
        "excluded_branch_ids": _unique_strings(
            item.get("excluded_branch_ids"), f"{field}.excluded_branch_ids"
        ),
        "reason": _required_text(item.get("reason"), f"{field}.reason"),
    }


def _validate_party(value: Any) -> dict[str, Any]:
    party = _object(value, "party")
    _only(
        party,
        "party",
        {
            "recommended_minimum",
            "recommended_maximum",
            "selected_size",
            "use_pregenerated_first",
            "members",
            "replacements",
        },
    )
    minimum = _optional_integer(party.get("recommended_minimum"), "recommended_minimum", 1)
    maximum = _optional_integer(party.get("recommended_maximum"), "recommended_maximum", 1)
    selected = _optional_integer(party.get("selected_size"), "selected_size", 1)
    if minimum is not None and maximum is not None and maximum < minimum:
        raise ValueError("party recommended maximum must not be below its minimum")
    if selected is not None and maximum is not None and selected != maximum:
        raise ValueError("party.selected_size must use the source-recommended maximum")
    members = [
        _validate_party_member(item, index)
        for index, item in enumerate(_list(party.get("members")))
    ]
    _require_unique(members, "actor_id", "party.members")
    replacements = [
        _validate_replacement(item, index)
        for index, item in enumerate(_list(party.get("replacements")))
    ]
    return {
        "recommended_minimum": minimum,
        "recommended_maximum": maximum,
        "selected_size": selected,
        "use_pregenerated_first": _boolean(
            party.get("use_pregenerated_first"), "use_pregenerated_first"
        ),
        "members": members,
        "replacements": replacements,
    }


def _validate_party_member(value: Any, index: int) -> dict[str, Any]:
    field = f"party.members[{index}]"
    item = _object(value, field)
    _only(
        item,
        field,
        {
            "actor_id",
            "name",
            "status",
            "source",
            "source_asset_path",
            "level",
            "xp",
            "hit_points",
            "resources",
            "equipment",
            "knowledge_scope_actor_id",
        },
    )
    actor_id = _required_text(item.get("actor_id"), f"{field}.actor_id")
    source = _choice(
        item.get("source"), f"{field}.source", {"pregen", "generated", "replacement"}
    )
    knowledge_actor = _required_text(
        item.get("knowledge_scope_actor_id"), f"{field}.knowledge_scope_actor_id"
    )
    if knowledge_actor != actor_id:
        raise ValueError(f"{field}.knowledge_scope_actor_id must equal actor_id")
    return {
        "actor_id": actor_id,
        "name": _required_text(item.get("name"), f"{field}.name"),
        "status": _choice(item.get("status"), f"{field}.status", ACTOR_STATUSES),
        "source": source,
        "source_asset_path": _text(item.get("source_asset_path")),
        "level": _integer(item.get("level"), f"{field}.level", minimum=1),
        "xp": _integer(item.get("xp"), f"{field}.xp", minimum=0),
        "hit_points": _json_object(item.get("hit_points"), f"{field}.hit_points"),
        "resources": _json_object(item.get("resources"), f"{field}.resources"),
        "equipment": _unique_strings(item.get("equipment"), f"{field}.equipment"),
        "knowledge_scope_actor_id": knowledge_actor,
    }


def _validate_replacement(value: Any, index: int) -> dict[str, str]:
    field = f"party.replacements[{index}]"
    item = _object(value, field)
    _only(item, field, {"predecessor_actor_id", "replacement_actor_id", "handoff_event_id"})
    predecessor = _required_text(
        item.get("predecessor_actor_id"), f"{field}.predecessor_actor_id"
    )
    replacement = _required_text(
        item.get("replacement_actor_id"), f"{field}.replacement_actor_id"
    )
    if predecessor == replacement:
        raise ValueError(f"{field} predecessor and replacement must be different actors")
    return {
        "predecessor_actor_id": predecessor,
        "replacement_actor_id": replacement,
        "handoff_event_id": _required_text(
            item.get("handoff_event_id"), f"{field}.handoff_event_id"
        ),
    }


def _validate_npc(value: Any, index: int) -> dict[str, Any]:
    field = f"npcs[{index}]"
    item = _object(value, field)
    _only(item, field, {"actor_id", "name", "status", "faction", "relationship", "notes"})
    return {
        "actor_id": _required_text(item.get("actor_id"), f"{field}.actor_id"),
        "name": _required_text(item.get("name"), f"{field}.name"),
        "status": _choice(item.get("status"), f"{field}.status", ACTOR_STATUSES),
        "faction": _text(item.get("faction")),
        "relationship": _text(item.get("relationship")),
        "notes": _text(item.get("notes")),
    }


def _validate_quest(value: Any, index: int) -> dict[str, Any]:
    field = f"quests[{index}]"
    item = _object(value, field)
    _only(item, field, {"id", "title", "status", "source_ref", "outcome"})
    return {
        "id": _required_text(item.get("id"), f"{field}.id"),
        "title": _required_text(item.get("title"), f"{field}.title"),
        "status": _choice(item.get("status"), f"{field}.status", QUEST_STATUSES),
        "source_ref": validate_source_ref(item.get("source_ref"), field=f"{field}.source_ref"),
        "outcome": _text(item.get("outcome")),
    }


def _validate_clue(value: Any, index: int) -> dict[str, Any]:
    field = f"clues[{index}]"
    item = _object(value, field)
    _only(item, field, {"id", "label", "status", "known_by_actor_ids", "source_ref"})
    return {
        "id": _required_text(item.get("id"), f"{field}.id"),
        "label": _required_text(item.get("label"), f"{field}.label"),
        "status": _choice(item.get("status"), f"{field}.status", CLUE_STATUSES),
        "known_by_actor_ids": _unique_strings(
            item.get("known_by_actor_ids"), f"{field}.known_by_actor_ids"
        ),
        "source_ref": validate_source_ref(item.get("source_ref"), field=f"{field}.source_ref"),
    }


def _validate_snapshot_dag(value: Any) -> dict[str, Any]:
    dag = _object(value, "snapshot_dag")
    _only(dag, "snapshot_dag", {"active_branch_id", "head_snapshot_id", "nodes"})
    nodes = []
    for index, raw in enumerate(_list(dag.get("nodes"))):
        field = f"snapshot_dag.nodes[{index}]"
        item = _object(raw, field)
        _only(
            item,
            field,
            {"id", "parent_id", "branch_id", "slot", "label", "checksum", "is_head"},
        )
        nodes.append(
            {
                "id": _required_text(item.get("id"), f"{field}.id"),
                "parent_id": _text(item.get("parent_id")),
                "branch_id": _required_text(item.get("branch_id"), f"{field}.branch_id"),
                "slot": _integer(item.get("slot"), f"{field}.slot", minimum=1),
                "label": _text(item.get("label")),
                "checksum": _required_text(item.get("checksum"), f"{field}.checksum"),
                "is_head": _boolean(item.get("is_head"), f"{field}.is_head"),
            }
        )
    _require_unique(nodes, "id", "snapshot_dag.nodes")
    return {
        "active_branch_id": _text(dag.get("active_branch_id")),
        "head_snapshot_id": _text(dag.get("head_snapshot_id")),
        "nodes": nodes,
    }


def _validate_random_projection(value: Any) -> dict[str, Any]:
    stream = _object(value, "random_stream")
    _only(stream, "random_stream", {"algorithm", "seed_fingerprint", "position"})
    return {
        "algorithm": _text(stream.get("algorithm")),
        "seed_fingerprint": _text(stream.get("seed_fingerprint")),
        "position": _integer(stream.get("position"), "random_stream.position", minimum=0),
    }


def _validate_ending(value: Any) -> dict[str, Any]:
    ending = _object(value, "ending")
    _only(
        ending,
        "ending",
        {
            "status",
            "conditions",
            "achieved_condition_id",
            "verification",
        },
    )
    conditions = [
        _validate_ending_condition(item, index)
        for index, item in enumerate(_list(ending.get("conditions")))
    ]
    _require_unique(conditions, "id", "ending.conditions")
    achieved = _text(ending.get("achieved_condition_id"))
    if achieved and achieved not in {item["id"] for item in conditions}:
        raise ValueError("ending.achieved_condition_id does not identify a declared condition")
    return {
        "status": _choice(ending.get("status"), "ending.status", ENDING_STATUSES),
        "conditions": conditions,
        "achieved_condition_id": achieved,
        "verification": [
            _json_object(item, f"ending.verification[{index}]")
            for index, item in enumerate(_list(ending.get("verification")))
        ],
    }


def _validate_ending_condition(value: Any, index: int) -> dict[str, Any]:
    field = f"ending.conditions[{index}]"
    item = _object(value, field)
    _only(item, field, {"id", "label", "source_ref", "all_of"})
    checks = [
        _validate_ending_check(check, check_index, field)
        for check_index, check in enumerate(_list(item.get("all_of")))
    ]
    if not checks:
        raise ValueError(f"{field}.all_of must contain at least one machine check")
    return {
        "id": _required_text(item.get("id"), f"{field}.id"),
        "label": _required_text(item.get("label"), f"{field}.label"),
        "source_ref": validate_source_ref(item.get("source_ref"), field=f"{field}.source_ref"),
        "all_of": checks,
    }


def _validate_ending_check(value: Any, index: int, parent: str) -> dict[str, Any]:
    field = f"{parent}.all_of[{index}]"
    item = _object(value, field)
    _only(item, field, {"kind", "path", "actor_id", "fact_key", "operator", "value"})
    kind = _choice(item.get("kind"), f"{field}.kind", CHECK_KINDS)
    path = _text(item.get("path"))
    actor_id = _text(item.get("actor_id"))
    fact_key = _text(item.get("fact_key"))
    if kind in {"manifest_value", "campaign_state_value", "actor_value"} and not path:
        raise ValueError(f"{field}.path is required for {kind}")
    if kind == "actor_value" and not actor_id:
        raise ValueError(f"{field}.actor_id is required for actor_value")
    if kind == "memory_fact" and not fact_key:
        raise ValueError(f"{field}.fact_key is required for memory_fact")
    return {
        "kind": kind,
        "path": path,
        "actor_id": actor_id,
        "fact_key": fact_key,
        "operator": _choice(item.get("operator"), f"{field}.operator", CHECK_OPERATORS),
        "value": deepcopy(item.get("value")),
    }


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return deepcopy(value)


def _json_object(value: Any, field: str) -> dict[str, Any]:
    return _object(value, field)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("manifest collection fields must be arrays")
    return deepcopy(value)


def _only(value: dict[str, Any], field: str, allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} contains unsupported fields: {', '.join(unknown)}")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_text(value: Any, field: str) -> str:
    result = _text(value)
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _integer(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer greater than or equal to {minimum}")
    return value


def _optional_integer(value: Any, field: str, minimum: int) -> int | None:
    return None if value is None else _integer(value, field, minimum=minimum)


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _choice(value: Any, field: str, choices: set[str]) -> str:
    result = _required_text(value, field)
    if result not in choices:
        raise ValueError(f"{field} must be one of {', '.join(sorted(choices))}")
    return result


def _unique_strings(value: Any, field: str) -> list[str]:
    result = [_required_text(item, f"{field}[]") for item in _list(value)]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _require_unique(items: list[dict[str, Any]], key: str, field: str) -> None:
    values = [item[key] for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicate {key} values")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
