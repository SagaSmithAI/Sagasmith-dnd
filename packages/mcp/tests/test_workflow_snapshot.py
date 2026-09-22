import hashlib
from pathlib import Path

from sagasmith_dnd_mcp.skills import SkillCatalog


def test_snapshot_keeps_content_and_provenance_until_refresh(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    refs = root / "references"
    refs.mkdir(parents=True)
    entry = root / "SKILL.md"
    asset = refs / "workflow.md"
    entry.write_bytes(b"# Original\n")
    asset.write_bytes(b"original workflow\n")
    catalog = SkillCatalog(dnd_root=root, modulegen_root=tmp_path / "absent")
    original_hash = catalog.package_hash()
    entry.write_bytes(b"# Changed\n")
    asset.write_bytes(b"changed workflow\n")
    assert catalog.read("dnd.root") == "# Original\n"
    assert catalog.read_asset("dnd:references/workflow.md") == "original workflow\n"
    for item in catalog.manifest():
        text = catalog.read(item["id"]) if ":" not in item["id"] else catalog.read_asset(item["id"])
        assert item["checksum"] == hashlib.sha256(text.encode()).hexdigest()
    assert catalog.package_hash() == original_hash
    catalog.refresh()
    assert catalog.read("dnd.root") == "# Changed\n"
    assert catalog.package_hash() != original_hash
    changed_hash = catalog.package_hash()
    asset.write_bytes(b"reference-only change\n")
    catalog.refresh()
    assert catalog.package_hash() != changed_hash


def test_snapshot_survives_deleted_source_and_rejects_traversal(tmp_path: Path) -> None:
    import pytest

    (tmp_path / "SKILL.md").write_bytes(b"# Saved\n")
    catalog = SkillCatalog(dnd_root=tmp_path, modulegen_root=tmp_path / "absent")
    catalog.list()
    (tmp_path / "SKILL.md").unlink()
    assert catalog.read("dnd.root") == "# Saved\n"
    with pytest.raises(LookupError):
        catalog.read_asset("dnd:../outside.md")
