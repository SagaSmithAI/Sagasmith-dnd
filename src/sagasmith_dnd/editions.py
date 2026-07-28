"""Shared D&D rules-edition normalization."""

from __future__ import annotations

from typing import Any


def normalize_dnd_edition(value: Any, *, default: str = "2014") -> str:
    """Return one canonical edition identifier or reject ambiguous input."""

    text = str(default if value is None or value == "" else value).strip().casefold()
    aliases = {
        "2014": "2014",
        "5.1": "2014",
        "2014 rules": "2014",
        "dnd 5e 2014": "2014",
        "dnd5e 2014": "2014",
        "2024": "2024",
        "5.2": "2024",
        "2024 rules": "2024",
        "dnd 5e 2024": "2024",
        "dnd5e 2024": "2024",
    }
    try:
        return aliases[text]
    except KeyError as exc:
        raise ValueError("edition must be 2014 or 2024") from exc
