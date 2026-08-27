from __future__ import annotations

import re
from pathlib import Path

from sagasmith_dnd_mcp.tool_profiles import CORE_TOOLS


def test_skill_reference_core_tools_match_runtime_policy() -> None:
    reference = (
        Path(__file__).parents[3] / "skills" / "full" / "references" / "mcp-contract.md"
    ).read_text(encoding="utf-8")
    paragraph = reference.split("Every connection starts\nwith exactly ", 1)[1].split(
        "\n\n", 1
    )[0]
    documented = re.findall(r"`([a-z][a-z0-9_]*)`", paragraph)

    assert paragraph.startswith(f"{len(CORE_TOOLS)} core tools:")
    assert len(documented) == len(CORE_TOOLS)
    assert set(documented) == set(CORE_TOOLS)
