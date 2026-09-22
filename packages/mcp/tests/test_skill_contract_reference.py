from __future__ import annotations

import re
from pathlib import Path


def test_modern_skill_workflows_do_not_require_legacy_negotiation() -> None:
    skills = Path(__file__).parents[3] / "skills"
    reference = (skills / "full/references/mcp-contract.md").read_text(encoding="utf-8")
    assert "stable public operation catalog" in reference
    assert "legacy-adapter.md" in reference
    assert "trusted identity" in reference
    legacy_instruction = re.compile(r"exposure\((?:action=|open|search|set)|tools/list_changed")
    modern = [skills / "SKILL.md", *skills.glob("skills/**/SKILL.md")]
    modern += list((skills / "full").rglob("*.md"))
    for path in modern:
        if path.name in {"legacy-adapter.md", "README.md", "README-en.md"}:
            continue
        assert not legacy_instruction.search(path.read_text(encoding="utf-8")), path


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
