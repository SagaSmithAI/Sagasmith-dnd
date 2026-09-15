import zipfile

from sagasmith_dnd_runtime.skills import SkillCatalog


def test_binary_dependencies_are_pinned_and_packaged_from_the_snapshot(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"# Fixture\n")
    (root / "image.png").write_bytes(b"\x89PNG-original")
    catalog = SkillCatalog(dnd_root=root, modulegen_root=tmp_path / "absent")
    before = catalog.package_hash()
    (root / "image.png").write_bytes(b"\x89PNG-changed")
    archive = catalog.publish(tmp_path / "output")
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("dnd/image.png") == b"\x89PNG-original"
    assert catalog.publish(tmp_path / "output") == archive
    catalog.refresh()
    assert catalog.package_hash() != before
