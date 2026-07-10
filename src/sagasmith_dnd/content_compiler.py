"""Build SagaSmith canonical D&D content packs from local source exports.

Foundry YAML is accepted only at build time.  The runtime consumes the JSON
pack emitted here, whose shape is intentionally owned by SagaSmith.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

CONTENT_SCHEMA_VERSION = 1
_SOURCE_DIRECTORIES = {
    "classes": "classes",
    "subclasses": "subclasses",
    "features": "classfeatures",
    "spells": "spells",
    "monsters": "monsters",
}


def compile_foundry_content(
    source_path: str | Path,
    *,
    ruleset_id: str = "dnd5e-2014",
    pack_id: str = "dnd5e-2014-srd",
) -> dict[str, Any]:
    """Compile a Foundry dnd5e source directory into a canonical content pack."""

    root = Path(source_path).expanduser().resolve()
    ruleset_id = {"2014": "dnd5e-2014", "2024": "dnd5e-2024"}.get(ruleset_id, ruleset_id)
    if not root.is_dir():
        raise ValueError(f"content source directory not found: {root}")
    content: dict[str, dict[str, Any]] = {key: {} for key in _SOURCE_DIRECTORIES}
    skipped: Counter[str] = Counter()
    for destination, directory in _SOURCE_DIRECTORIES.items():
        source_dir = root / directory
        if not source_dir.is_dir():
            skipped[f"missing_directory:{directory}"] += 1
            continue
        for file in sorted(source_dir.rglob("*.yml")):
            raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict) or not _is_2014(raw):
                continue
            key = _source_key(raw, file)
            if not key:
                skipped["missing_identifier"] += 1
                continue
            try:
                content[destination][key] = _compile_entry(
                    destination,
                    raw,
                    file.relative_to(root),
                )
            except (TypeError, ValueError):
                skipped[f"invalid:{destination}"] += 1
                content[destination].pop(key, None)
                continue
    coverage = {
        key: {
            "total": len(values),
            "executable": sum(
                1 for value in values.values() if value.get("coverage") == "executable"
            ),
            "partial": sum(1 for value in values.values() if value.get("coverage") == "partial"),
        }
        for key, values in content.items()
    }
    return {
        "schema_version": CONTENT_SCHEMA_VERSION,
        "id": pack_id,
        "ruleset_id": ruleset_id,
        "source_format": "foundry-dnd5e-yaml",
        "content": content,
        "coverage": coverage,
        "skipped": dict(sorted(skipped.items())),
    }


def write_content_pack(pack: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output


def _compile_entry(kind: str, raw: dict[str, Any], source_path: Path) -> dict[str, Any]:
    raw_system = dict(raw.get("system") or {})
    system = _canonical_system(kind, raw_system)
    source_key = _source_key(raw, source_path)
    common = {
        "source_key": source_key,
        "foundry_id": str(raw.get("_id") or ""),
        "name": str(raw.get("name") or source_key),
        "img": str(raw.get("img") or ""),
        "source": _source_metadata(raw_system, source_path),
    }
    if kind == "classes":
        return {
            **common,
            "hit_die": str(system.get("hitDice") or ""),
            "advancement": list(system.get("advancement") or []),
            "feature_grants": _feature_grants(system),
            "save_proficiencies": _save_proficiencies(system),
            "system": system,
            "coverage": "partial",
        }
    if kind == "subclasses":
        return {
            **common,
            "class_key": str(system.get("classIdentifier") or ""),
            "advancement": list(system.get("advancement") or []),
            "feature_grants": _feature_grants(system),
            "system": system,
            "coverage": "partial",
        }
    if kind == "features":
        activities = _compile_activities(raw_system)
        return {
            **common,
            "item_type": str(raw.get("type") or "feat"),
            "system": system,
            "effects": _dict_list(raw.get("effects")),
            "activities": activities,
            "coverage": _coverage(activities),
        }
    if kind == "spells":
        activities = _compile_activities(raw_system, spell=True)
        return {
            **common,
            "system": system,
            "effects": _dict_list(raw.get("effects")),
            "activities": activities,
            "coverage": _coverage(activities),
        }
    if kind == "monsters":
        return {
            **common,
            "system": system,
            "prototype_token": _canonical_token(raw.get("prototypeToken")),
            "items": [_compile_item(item) for item in _dict_list(raw.get("items"))],
            "coverage": "executable" if raw.get("items") else "partial",
        }
    raise ValueError(f"unknown content kind: {kind}")


def _compile_item(raw: dict[str, Any]) -> dict[str, Any]:
    raw_system = dict(raw.get("system") or {})
    system = _canonical_system("item", raw_system)
    return {
        "name": str(raw.get("name") or "Action"),
        "type": str(raw.get("type") or "feat"),
        "source_key": _source_key(raw, Path("item.yml")),
        "system": system,
        "effects": _dict_list(raw.get("effects")),
        "activities": _compile_activities(raw_system),
    }


def _compile_activities(system: dict[str, Any], *, spell: bool = False) -> list[dict[str, Any]]:
    activities = []
    for key, raw in dict(system.get("activities") or {}).items():
        if not isinstance(raw, dict):
            continue
        activity_type = "cast" if spell else str(raw.get("type") or "utility")
        activity_system = {
            "foundry_id": raw.get("_id") or key,
            "attack": _clean(raw.get("attack")),
            "damage": _clean(raw.get("damage")),
            "healing": _clean(raw.get("healing")),
            "save": _clean(raw.get("save")),
            "check": _clean(raw.get("check")),
            "level": system.get("level", 0),
            "properties": list(system.get("properties") or []),
            "concentration": bool((raw.get("duration") or {}).get("concentration")),
        }
        attack = dict(raw.get("attack") or {})
        if attack.get("bonus") not in (None, ""):
            activity_system["attack_bonus"] = attack["bonus"]
        activities.append(
            {
                "source_key": str(raw.get("_id") or key),
                "name": str(raw.get("name") or system.get("identifier") or "Activity"),
                "type": activity_type,
                "activation": _clean(raw.get("activation") or system.get("activation")),
                "consumption": _clean(raw.get("consumption")),
                "duration": _clean(raw.get("duration") or system.get("duration")),
                "effects": _dict_list(raw.get("effects")),
                "range": _clean(raw.get("range") or system.get("range")),
                "target": _clean(raw.get("target") or system.get("target")),
                "uses": _clean(raw.get("uses") or system.get("uses")),
                "system": activity_system,
            }
        )
    return activities


def _coverage(activities: list[dict[str, Any]]) -> str:
    return "executable" if activities else "partial"


def _source_metadata(system: dict[str, Any], source_path: Path) -> dict[str, Any]:
    source = dict(system.get("source") or {})
    return {
        "rules": str(source.get("rules") or "2014"),
        "license": str(source.get("license") or ""),
        "book": str(source.get("book") or ""),
        "path": source_path.as_posix(),
    }


def _is_2014(raw: dict[str, Any]) -> bool:
    source = dict(dict(raw.get("system") or {}).get("source") or {})
    return str(source.get("rules") or "2014") == "2014"


def _source_key(raw: dict[str, Any], source_path: Path) -> str:
    system = dict(raw.get("system") or {})
    value = system.get("identifier") or raw.get("identifier") or raw.get("_id") or source_path.stem
    return str(value).strip().lower().replace("_", "-").replace(" ", "-")


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _clean_system(value: Any) -> dict[str, Any]:
    return _clean(value) if isinstance(value, dict) else {}


def _canonical_system(kind: str, system: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "classes": {"identifier", "hitDice", "advancement", "spellcasting", "levels"},
        "subclasses": {"identifier", "classIdentifier", "advancement"},
        "features": {"identifier", "uses", "type", "requirements", "properties"},
        "spells": {
            "identifier",
            "activation",
            "duration",
            "target",
            "range",
            "uses",
            "level",
            "school",
            "materials",
            "preparation",
            "properties",
        },
        "monsters": {
            "abilities",
            "attributes",
            "details",
            "traits",
            "skills",
            "currency",
            "spells",
            "bonuses",
        },
        "item": {
            "identifier",
            "uses",
            "properties",
            "equipped",
            "activation",
            "duration",
            "target",
            "range",
            "damage",
            "ability",
            "attack",
            "save",
            "formula",
            "level",
        },
    }
    return {key: _clean(value) for key, value in system.items() if key in fields.get(kind, set())}


def _canonical_token(value: Any) -> dict[str, Any]:
    token = dict(value or {})
    fields = {"name", "disposition", "width", "height", "scale", "sight", "detectionModes"}
    return {key: _clean(item) for key, item in token.items() if key in fields}


def _feature_grants(system: dict[str, Any]) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    for advancement in system.get("advancement") or []:
        if not isinstance(advancement, dict) or str(advancement.get("type") or "") != "ItemGrant":
            continue
        configuration = dict(advancement.get("configuration") or {})
        level = int(advancement.get("level") or 1)
        for item in configuration.get("items") or []:
            if not isinstance(item, dict) or item.get("optional"):
                continue
            reference = str(item.get("uuid") or "")
            foundry_id = reference.rsplit(".", 1)[-1] if reference else ""
            if foundry_id:
                grants.append({"level": level, "foundry_id": foundry_id})
    return grants


def _save_proficiencies(system: dict[str, Any]) -> list[str]:
    values = []
    for advancement in system.get("advancement") or []:
        if not isinstance(advancement, dict) or str(advancement.get("type") or "") != "Trait":
            continue
        if int(advancement.get("level") or 1) != 1:
            continue
        configuration = dict(advancement.get("configuration") or {})
        values.extend(
            str(value).removeprefix("saves:")
            for value in configuration.get("grants") or []
            if str(value).startswith("saves:")
        )
    return sorted(set(values))


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value
