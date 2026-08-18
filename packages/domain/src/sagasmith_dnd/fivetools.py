"""Import source-corroborated 5etools monster records without runtime coupling.

This adapter is intentionally a build-time boundary.  It resolves 5etools'
copy/modifier representation and renders ordinary 2014 statblock Markdown;
SagaSmith's normal statblock compiler remains the only runtime authority.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_SIZE = {"T": "Tiny", "S": "Small", "M": "Medium", "L": "Large", "H": "Huge", "G": "Gargantuan"}
_ALIGNMENT = {
    "L": "lawful",
    "N": "neutral",
    "C": "chaotic",
    "G": "good",
    "E": "evil",
    "U": "unaligned",
    "A": "any alignment",
}
_ABILITY = ("str", "dex", "con", "int", "wis", "cha")


def load_fivetools_bestiary(paths: Iterable[str | Path]) -> dict[tuple[str, str], dict[str, Any]]:
    """Load a detached monster registry keyed by exact source and name."""

    registry: dict[tuple[str, str], dict[str, Any]] = {}
    for path_value in paths:
        path = Path(path_value)
        document = json.loads(path.read_text(encoding="utf-8"))
        for raw in document.get("monster") or []:
            monster = copy.deepcopy(dict(raw))
            names = {str(monster["name"]), str(monster.get("ENG_name") or "")}
            for name in names:
                if name:
                    key = (str(monster["source"]).casefold(), name.casefold())
                    registry.setdefault(key, monster)
    return registry


def _walk_replace(value: Any, pattern: re.Pattern[str], replacement: str) -> Any:
    if isinstance(value, str):
        return pattern.sub(replacement, value)
    if isinstance(value, list):
        return [_walk_replace(item, pattern, replacement) for item in value]
    if isinstance(value, dict):
        return {key: _walk_replace(item, pattern, replacement) for key, item in value.items()}
    return copy.deepcopy(value)


def _array_items(value: Any) -> list[Any]:
    return copy.deepcopy(value if isinstance(value, list) else [value])


def _apply_array_mod(target: dict[str, Any], field: str, modifier: Mapping[str, Any]) -> None:
    mode = str(modifier.get("mode") or "")
    values = list(target.get(field) or [])
    items = _array_items(modifier.get("items"))
    if mode == "prependArr":
        target[field] = [*items, *values]
    elif mode == "appendArr":
        target[field] = [*values, *items]
    elif mode == "appendIfNotExistsArr":
        target[field] = [*values, *(item for item in items if item not in values)]
    elif mode == "insertArr":
        index = int(modifier.get("index") or 0)
        target[field] = [*values[:index], *items, *values[index:]]
    elif mode == "removeArr":
        names = modifier.get("names")
        remove = {str(item).casefold() for item in (names if isinstance(names, list) else [names])}
        target[field] = [
            item
            for item in values
            if str(item.get("name") if isinstance(item, dict) else item).casefold() not in remove
        ]
    elif mode == "replaceArr":
        replace = str(modifier.get("replace") or "").casefold()
        replaced = False
        output = []
        for item in values:
            names = (
                {
                    str(item.get("name") or "").casefold(),
                    str(item.get("ENG_name") or "").casefold(),
                }
                if isinstance(item, dict)
                else {str(item).casefold()}
            )
            if not replaced and replace in names:
                output.extend(items)
                replaced = True
            else:
                output.append(item)
        if not replaced:
            raise ValueError(f"5etools replaceArr target is absent: {field}.{replace}")
        target[field] = output
    else:
        raise ValueError(f"unsupported 5etools array modifier: {mode}")


def resolve_fivetools_monster(
    raw: Mapping[str, Any],
    registry: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    _stack: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Resolve `_copy` and the deterministic modifier modes used by bestiaries."""

    value = copy.deepcopy(dict(raw))
    copy_spec = value.pop("_copy", None)
    if not isinstance(copy_spec, Mapping):
        return value
    key = (str(copy_spec["source"]).casefold(), str(copy_spec["name"]).casefold())
    if key in _stack:
        raise ValueError(f"cyclic 5etools monster copy: {key}")
    base = registry.get(key)
    if base is None:
        raise LookupError(
            f"missing 5etools base monster: {copy_spec['source']}:{copy_spec['name']}"
        )
    result = resolve_fivetools_monster(base, registry, _stack=(*_stack, key))
    modifiers = copy.deepcopy(dict(copy_spec.get("_mod") or {}))
    for field, raw_modifiers in modifiers.items():
        for modifier in raw_modifiers if isinstance(raw_modifiers, list) else [raw_modifiers]:
            if not isinstance(modifier, Mapping):
                raise ValueError("5etools modifiers must be objects")
            if modifier.get("mode") == "replaceTxt":
                flags = re.IGNORECASE if "i" in str(modifier.get("flags") or "") else 0
                pattern = re.compile(str(modifier["replace"]), flags)
                result = _walk_replace(result, pattern, str(modifier.get("with") or ""))
            elif field == "_":
                raise ValueError(f"unsupported 5etools structural modifier: {modifier.get('mode')}")
            else:
                _apply_array_mod(result, field, modifier)
    # Explicit fields are authoritative after inherited modifiers. `_trait` is
    # provenance for the printed racial/template variant; the record's actual
    # mechanical overrides remain explicit in the source entry.
    result.update(copy.deepcopy(value))
    result["_resolved_copy"] = {
        "source": copy_spec["source"],
        "name": copy_spec["name"],
        "trait": copy.deepcopy(copy_spec.get("_trait")),
    }
    return result


def _tag_text(value: str) -> str:
    attack = {
        "mw": "Melee Weapon Attack:",
        "rw": "Ranged Weapon Attack:",
        "ms": "Melee Spell Attack:",
        "rs": "Ranged Spell Attack:",
        "m": "Melee Attack:",
        "r": "Ranged Attack:",
        "mw,rw": "Melee or Ranged Weapon Attack:",
        "rw,mw": "Melee or Ranged Weapon Attack:",
        "ms,rs": "Melee or Ranged Spell Attack:",
        "rs,ms": "Melee or Ranged Spell Attack:",
    }

    def replace(match: re.Match[str]) -> str:
        tag = match.group(1)
        body = match.group(2) or ""
        first = body.split("|", 1)[0]
        if tag == "atk":
            return attack.get(first, "Attack:")
        if tag == "hit":
            return f"{first if first.startswith(('+', '-')) else '+' + first}"
        if tag in {"h", "hom"}:
            return "Hit: "
        if tag == "dc":
            return f"DC {first}"
        if tag == "recharge":
            return f"(Recharge {first or '6'})"
        return first

    return re.sub(r"\{@([a-zA-Z]+)(?:\s+([^{}]*))?}", replace, value).replace("��", "'")


def _entry_text(value: Any) -> str:
    if isinstance(value, str):
        return _tag_text(value)
    if isinstance(value, list):
        return " ".join(filter(None, (_entry_text(item) for item in value)))
    if not isinstance(value, Mapping):
        return str(value)
    kind = value.get("type")
    if kind == "list":
        return " ".join(f"• {_entry_text(item)}" for item in value.get("items") or [])
    if kind == "item":
        name = _tag_text(str(value.get("name") or ""))
        body = _entry_text(value.get("entry") or value.get("entries") or [])
        return f"{name} {body}".strip()
    if kind == "entries":
        return _entry_text(value.get("entries") or [])
    return _entry_text(value.get("entries") or value.get("entry") or "")


def _named_entries(values: Sequence[Mapping[str, Any]] | None) -> list[str]:
    result = []
    for item in values or []:
        name = _tag_text(str(item.get("ENG_name") or item.get("name") or "Feature"))
        text = _entry_text(item.get("entries") or item.get("entry") or [])
        result.append(f"***{name}.*** {text}".strip())
    return result


def _damage_field(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(filter(None, (_damage_field(item) for item in value)))
    if isinstance(value, Mapping):
        for key in ("vulnerable", "resist", "immune", "conditionImmune"):
            if key in value:
                body = _damage_field(value[key])
                prefix = str(value.get("preNote") or "")
                suffix = str(value.get("note") or "")
                return " ".join(item for item in (prefix, body, suffix) if item)
    return str(value)


def _type_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        tags = value.get("tags") or []
        suffix = f" ({', '.join(str(item) for item in tags)})" if tags else ""
        return f"{value.get('type', 'creature')}{suffix}"
    return "creature"


def _alignment(value: Any) -> str:
    if not isinstance(value, list):
        return "unaligned"
    words = [_ALIGNMENT.get(str(item), str(item)) for item in value if str(item) != "NX"]
    return " ".join(words) or "unaligned"


def _ac(value: Any) -> str:
    entries = value if isinstance(value, list) else [value]
    rendered = []
    for item in entries:
        if isinstance(item, Mapping):
            base = str(item.get("ac") or item.get("special") or "")
            detail = item.get("condition") or ", ".join(str(x) for x in item.get("from") or [])
            rendered.append(f"{base} ({_tag_text(str(detail))})" if detail else base)
        else:
            rendered.append(str(item))
    return ", ".join(filter(None, rendered))


def _speed(value: Any) -> str:
    if not isinstance(value, Mapping):
        return str(value)
    result = []
    for mode, raw in value.items():
        if mode in {"canHover", "alternate"}:
            continue
        amount = raw.get("number") if isinstance(raw, Mapping) else raw
        condition = raw.get("condition") if isinstance(raw, Mapping) else None
        prefix = "" if mode == "walk" else f"{mode} "
        text = f"{prefix}{amount} ft."
        if condition:
            text += f" {_tag_text(str(condition))}"
        result.append(text)
    return ", ".join(result) or "0 ft."


def fivetools_monster_markdown(monster: Mapping[str, Any]) -> str:
    """Render a resolved 5etools monster through the normal statblock parser."""

    value = dict(monster)
    missing = [
        field for field in (*_ABILITY, "size", "type", "ac", "hp", "speed") if field not in value
    ]
    if missing:
        raise ValueError("resolved 5etools monster is missing: " + ", ".join(missing))
    size = _SIZE.get(str(value["size"]), str(value["size"]))
    hp = value["hp"]
    hp_text = (
        f"{hp.get('average')} ({hp.get('formula')})"
        if isinstance(hp, Mapping) and hp.get("formula")
        else str(hp.get("special") if isinstance(hp, Mapping) else hp)
    )
    lines = [
        f"# {value['name']}",
        "",
        f"*{size} {_type_text(value['type'])}, {_alignment(value.get('alignment'))}*",
        "",
        f"**Armor Class** {_ac(value['ac'])}",
        "",
        f"**Hit Points** {hp_text}",
        "",
        f"**Speed** {_speed(value['speed'])}",
        "",
        "| STR | DEX | CON | INT | WIS | CHA |",
        "|---:|---:|---:|---:|---:|---:|",
        "| "
        + " | ".join(f"{int(value[key])} ({(int(value[key]) - 10) // 2:+d})" for key in _ABILITY)
        + " |",
        "",
    ]
    fields = (
        ("Saving Throws", value.get("save")),
        ("Skills", value.get("skill")),
        ("Damage Vulnerabilities", value.get("vulnerable")),
        ("Damage Resistances", value.get("resist")),
        ("Damage Immunities", value.get("immune")),
        ("Condition Immunities", value.get("conditionImmune")),
        (
            "Senses",
            [*(value.get("senses") or []), f"passive Perception {value.get('passive', 10)}"],
        ),
        ("Languages", value.get("languages") or ["—"]),
        ("Challenge", value.get("cr", "0")),
    )
    for label, raw in fields:
        if raw in (None, [], {}):
            continue
        if isinstance(raw, Mapping):
            text = ", ".join(f"{key.title()} {item}" for key, item in raw.items())
        elif isinstance(raw, list):
            text = _damage_field(raw)
        else:
            text = _damage_field(raw)
        lines.extend((f"**{label}** {_tag_text(text)}", ""))
    lines.extend(_named_entries(value.get("trait")))
    spellcasting = []
    for item in value.get("spellcasting") or []:
        entries = [*(item.get("headerEntries") or [])]
        for level, detail in (item.get("spells") or {}).items():
            spells = ", ".join(_tag_text(str(spell)) for spell in detail.get("spells") or [])
            slots = detail.get("slots")
            entries.append(
                f"{level} level" + (f" ({slots} slots)" if slots else "") + f": {spells}"
            )
        spellcasting.append({"name": item.get("name") or "Spellcasting", "entries": entries})
    lines.extend(_named_entries(spellcasting))
    for title, key in (
        ("Actions", "action"),
        ("Bonus Actions", "bonus"),
        ("Reactions", "reaction"),
        ("Legendary Actions", "legendary"),
        ("Mythic Actions", "mythic"),
    ):
        entries = _named_entries(value.get(key))
        if entries:
            lines.extend(("", f"## {title}", "", *entries))
    return "\n".join(lines).strip()


__all__ = [
    "fivetools_monster_markdown",
    "load_fivetools_bestiary",
    "resolve_fivetools_monster",
]
