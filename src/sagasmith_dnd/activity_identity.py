"""Stable identities for source-authored activities.

Display names preserve the rulebook wording, including form or equipment
qualifiers. Runtime behavior must use mechanic identities instead of requiring
that display text equal one unqualified English title.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MULTIATTACK_MECHANIC_ID = "dnd5e.core.action.multiattack_choice"

_MULTIATTACK_SOURCE_NAME = re.compile(
    r"multiattack(?:\s*\([^()\r\n]+\))?",
    re.IGNORECASE,
)


def is_multiattack_source_name(value: object) -> bool:
    """Return whether a rulebook heading denotes the Multiattack action.

    Monster statblocks use parenthetical qualifiers such as ``(Yuan-ti Form
    Only)``. The full match deliberately excludes similarly named class
    features such as ``Multiattack Defense``.
    """

    normalized = " ".join(str(value or "").split())
    return bool(_MULTIATTACK_SOURCE_NAME.fullmatch(normalized))


def is_multiattack_activity(activity: Mapping[str, Any]) -> bool:
    """Return whether an activity has the canonical Multiattack identity.

    New cards carry an explicit mechanic reference. Strict source-name
    recognition remains as a compatibility path for existing actor cards.
    """

    mechanic_refs = activity.get("mechanic_refs") or []
    if isinstance(mechanic_refs, (list, tuple)) and MULTIATTACK_MECHANIC_ID in {
        str(item).strip() for item in mechanic_refs
    }:
        return True
    return is_multiattack_source_name(activity.get("name"))
