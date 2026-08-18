from pathlib import Path

from sagasmith_dnd.bundled_rules import (
    build_bundled_rule_sources,
    bundled_rule_corpus_inventory,
)


def test_bundled_rule_catalog_partitions_every_srd_file_once() -> None:
    workspace = Path(__file__).resolve().parents[3]
    root = workspace / "skills" / "full" / "skills" / "dnd-dm" / "srd"

    sources = build_bundled_rule_sources(root)
    inventory = bundled_rule_corpus_inventory(root, sources)

    assert inventory["complete"] is True
    assert inventory["files"] == 2032
    assert inventory["sources"] == 42
    assert inventory["corpora"] == {
        "2014:en": {"edition": "2014", "locale": "en", "files": 1021, "sources": 11},
        "2014:zh": {"edition": "2014", "locale": "zh", "files": 991, "sources": 11},
        "2024:en": {"edition": "2024", "locale": "en", "files": 20, "sources": 20},
    }
    assert len({source.source_key for source in sources}) == len(sources)
    assert all(source.content.strip() for source in sources)
    assert all(len(source.checksum) == 64 for source in sources)
    assert all(source.metadata()["complete_partition"] is True for source in sources)
