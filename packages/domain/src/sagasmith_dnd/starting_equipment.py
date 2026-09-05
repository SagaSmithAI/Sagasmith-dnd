"""Strict, source-bound starting-equipment selection mechanics."""

from __future__ import annotations

import copy
import re
from dataclasses import asdict
from typing import Any, Mapping

from sagasmith_dnd.character_schema import add_inventory_item, adjust_wallet, validate_character_sheet
from sagasmith_dnd.engine import roll

_DICE = re.compile(r"^[1-9]\d*d[1-9]\d*$", re.IGNORECASE)
_DENOMINATIONS = {"cp", "sp", "ep", "gp", "pp"}


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{field} has unsupported fields: {sorted(unknown)}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def normalize_starting_equipment_contract(raw: Any) -> dict[str, Any]:
    """Validate and normalize a reviewed starting-equipment contract."""

    if not isinstance(raw, Mapping):
        raise ValueError("starting equipment contract must be an object")
    _reject_unknown(raw, {"items", "choices", "gold_alternative"}, "starting equipment")
    items_raw = raw.get("items", [])
    choices_raw = raw.get("choices", [])
    if not isinstance(items_raw, list) or not isinstance(choices_raw, list):
        raise ValueError("starting equipment items and choices must be arrays")
    items: list[dict[str, Any]] = []
    item_ids: set[str] = set()
    for index, item in enumerate(items_raw):
        field = f"starting equipment items[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{field} must be an object")
        _reject_unknown(item, {"artifact_id", "quantity"}, field)
        artifact_id = _text(item.get("artifact_id"), f"{field}.artifact_id")
        quantity = _positive_int(item.get("quantity"), f"{field}.quantity")
        if artifact_id in item_ids:
            raise ValueError("starting equipment items must be distinct")
        item_ids.add(artifact_id)
        items.append({"artifact_id": artifact_id, "quantity": quantity})

    choices: list[dict[str, Any]] = []
    choice_ids: set[str] = set()
    for index, choice in enumerate(choices_raw):
        field = f"starting equipment choices[{index}]"
        if not isinstance(choice, Mapping):
            raise ValueError(f"{field} must be an object")
        _reject_unknown(choice, {"id", "count", "options", "allow_duplicates"}, field)
        group_id = _text(choice.get("id"), f"{field}.id")
        count = _positive_int(choice.get("count"), f"{field}.count")
        options_raw = choice.get("options")
        if not isinstance(options_raw, list) or not options_raw:
            raise ValueError(f"{field}.options must be a non-empty array")
        options = [_text(value, f"{field}.options[{i}]") for i, value in enumerate(options_raw)]
        if len(set(options)) != len(options):
            raise ValueError(f"{field}.options must be distinct")
        allow_duplicates = choice.get("allow_duplicates", False)
        if not isinstance(allow_duplicates, bool):
            raise ValueError(f"{field}.allow_duplicates must be a boolean")
        if group_id in choice_ids:
            raise ValueError("starting equipment choice ids must be distinct")
        choice_ids.add(group_id)
        choices.append(
            {
                "id": group_id,
                "count": count,
                "options": options,
                "allow_duplicates": allow_duplicates,
            }
        )

    gold_raw = raw.get("gold_alternative")
    gold: dict[str, Any] | None = None
    if gold_raw is not None:
        if not isinstance(gold_raw, Mapping):
            raise ValueError("starting equipment gold_alternative must be an object")
        _reject_unknown(
            gold_raw,
            {"dice", "multiplier", "denomination", "replaces_background_equipment"},
            "starting equipment gold_alternative",
        )
        dice = _text(gold_raw.get("dice"), "starting equipment gold_alternative.dice")
        if not _DICE.fullmatch(dice):
            raise ValueError("starting equipment gold_alternative.dice must be NdS")
        multiplier = _positive_int(
            gold_raw.get("multiplier"), "starting equipment gold_alternative.multiplier"
        )
        denomination = _text(
            gold_raw.get("denomination"), "starting equipment gold_alternative.denomination"
        ).casefold()
        if denomination not in _DENOMINATIONS:
            raise ValueError("starting equipment gold_alternative.denomination is invalid")
        replaces = gold_raw.get("replaces_background_equipment")
        if not isinstance(replaces, bool):
            raise ValueError(
                "starting equipment gold_alternative.replaces_background_equipment must be a boolean"
            )
        gold = {
            "dice": dice,
            "multiplier": multiplier,
            "denomination": denomination,
            "replaces_background_equipment": replaces,
        }
    if not items and not choices and gold is None:
        raise ValueError("starting equipment contract must offer equipment or gold")
    return {"items": items, "choices": choices, "gold_alternative": gold}


def apply_starting_equipment(
    sheet: dict[str, Any],
    *,
    contract: Mapping[str, Any],
    selection: Mapping[str, Any],
    item_templates: Mapping[str, Mapping[str, Any]],
    source_key: str,
    rng: Any = None,
) -> dict[str, Any]:
    """Apply one validated contract without mutating inputs or auto-equipping items."""

    normalized = normalize_starting_equipment_contract(contract)
    if not isinstance(selection, Mapping):
        raise ValueError("starting equipment selection must be an object")
    _reject_unknown(selection, {"mode", "choices"}, "starting equipment selection")
    mode = _text(selection.get("mode"), "starting equipment selection.mode").casefold()
    if mode not in {"equipment", "gold"}:
        raise ValueError("starting equipment selection.mode must be equipment or gold")
    source = _text(source_key, "starting equipment source_key")
    # Validate all templates and selection choices before touching the RNG.
    templates = {str(key): value for key, value in item_templates.items()}
    for artifact_id, template in templates.items():
        if not isinstance(template, Mapping):
            raise ValueError(f"starting equipment template is not an object: {artifact_id}")
    gold = normalized["gold_alternative"]
    if mode == "gold":
        if gold is None:
            raise ValueError("starting equipment contract has no gold alternative")
        if "choices" in selection:
            raise ValueError("gold starting equipment selection cannot include choices")
    else:
        if gold is not None and not isinstance(selection.get("choices", {}), Mapping):
            raise ValueError("starting equipment choices must be an object")
        chosen = selection.get("choices", {})
        if not isinstance(chosen, Mapping):
            raise ValueError("starting equipment choices must be an object")
        groups = {item["id"]: item for item in normalized["choices"]}
        if set(chosen) != set(groups):
            raise ValueError("starting equipment choices must answer exactly the reviewed groups")
        for group_id, spec in groups.items():
            values = chosen[group_id]
            if not isinstance(values, list) or len(values) != spec["count"]:
                raise ValueError(f"starting equipment choice {group_id} has the wrong count")
            if any(value not in spec["options"] for value in values):
                raise ValueError(f"starting equipment choice {group_id} contains an unavailable option")
            if not spec["allow_duplicates"] and len(set(values)) != len(values):
                raise ValueError(f"starting equipment choice {group_id} does not allow duplicates")
        selected_ids = [item["artifact_id"] for item in normalized["items"]]
        selected_ids.extend(value for values in chosen.values() for value in values)
        if any(artifact_id not in templates for artifact_id in selected_ids):
            missing = sorted({artifact_id for artifact_id in selected_ids if artifact_id not in templates})
            raise ValueError("starting equipment template is missing: " + ", ".join(missing))

    result_sheet = validate_character_sheet(copy.deepcopy(sheet))
    item_ids: list[str] = []
    wallet: dict[str, int] = {}
    roll_result: dict[str, Any] | None = None
    if mode == "gold":
        if rng is None:
            raise ValueError("gold starting equipment requires an explicit rng")
        rolled = roll(gold["dice"], rng=rng)
        roll_result = asdict(rolled)
        amount = int(rolled.total) * gold["multiplier"]
        result_sheet = adjust_wallet(result_sheet, gold["denomination"], amount)
        wallet[gold["denomination"]] = amount
    else:
        chosen = selection.get("choices", {})
        entries = list(normalized["items"])
        entries.extend(
            {"artifact_id": artifact_id, "quantity": 1}
            for values in chosen.values()
            for artifact_id in values
        )
        for entry in entries:
            item = copy.deepcopy(dict(templates[entry["artifact_id"]]))
            item.pop("id", None)
            item["quantity"] = entry["quantity"]
            item["source_key"] = source
            result_sheet, item_id = add_inventory_item(result_sheet, item)
            item_ids.append(item_id)
    recorded_selection = {"mode": mode}
    if mode == "equipment":
        recorded_selection["choices"] = copy.deepcopy(dict(selection.get("choices", {})))
    return {
        "sheet": result_sheet,
        "selection": recorded_selection,
        "item_ids": item_ids,
        "wallet": wallet,
        "roll": roll_result,
        "replaces_background_equipment": bool(gold and gold["replaces_background_equipment"])
        if mode == "gold"
        else False,
    }
