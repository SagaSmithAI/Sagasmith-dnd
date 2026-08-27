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


def test_map_reference_uses_native_audience_safe_media_contract() -> None:
    reference = (
        Path(__file__).parents[3]
        / "skills"
        / "full"
        / "skills"
        / "dnd-dm"
        / "references"
        / "DM_MAP_SYS.md"
    ).read_text(encoding="utf-8")

    assert 'view="render"' in reference
    assert '"audience_projection": "party_public"' in reference
    assert "ImageContent" in reference
    assert "固定为每格五尺" in reference
    for stale_contract in ("Microsoft Excel", "飞书", ".xlsx", "10尺", "每轮更新"):
        assert stale_contract not in reference
