from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from sagasmith_dnd import cli, official_expansions
from sagasmith_dnd.official_expansions import (
    OFFICIAL_EXPANSION_LOCK_SCHEMA,
    load_official_expansion_lock,
    official_expansion_catalog,
    resolve_official_expansion_archives,
    verify_official_expansion_library,
)


def test_shipped_lock_covers_every_current_official_expansion_artifact() -> None:
    lock = load_official_expansion_lock()
    content = Counter()
    ready = Counter()
    catalog_only = Counter()
    for package in lock["packages"]:
        content.update(package["content_summary"])
        ready.update(package["selection_ready"])
        catalog_only.update(package["catalog_only"])

    assert len(lock["packages"]) == 10
    assert sum(content.values()) == 2007
    assert sum(ready.values()) == 1134
    assert sum(catalog_only.values()) == 873
    assert ready == {
        "background": 51,
        "class": 1,
        "feat": 71,
        "feature": 504,
        "item": 156,
        "species": 106,
        "spell": 168,
        "subclass": 77,
    }
    assert ready + catalog_only == content


def test_shipped_registry_is_2014_only_and_metadata_only() -> None:
    catalog = official_expansion_catalog("2014")

    assert len(catalog) == 10
    assert official_expansion_catalog("2024") == ()
    assert all(item["editions"] == ["2014"] for item in catalog)
    assert all("path" not in item and "sources" not in item for item in catalog)


def _fixture_library(root: Path) -> tuple[dict, Path]:
    content_root = root / "content-library"
    packages_root = content_root / "packages"
    packages_root.mkdir(parents=True)
    package = {
        "id": "dnd5e.addon.fixture.official",
        "version": "1.0.0",
        "checksum": "1" * 64,
        "system_id": "dnd5e",
        "kind": "addon",
        "manifest": {
            "title": "Fixture Supplement",
            "classification": "official_supplement",
            "editions": ["2014"],
            "content_summary": {"class": 1, "subclass": 1},
        },
        "metadata": {
            "title": "Fixture Supplement",
            "distribution": "private",
            "license": "user-supplied",
        },
        "sources": [
            {
                "authority": "supplement",
                "publication_id": "fixture2014",
            }
        ],
        "content": {
            "artifacts": [
                {"kind": "class", "application_state": "selection_ready"},
                {"kind": "subclass", "application_state": "selection_ready"},
            ]
        },
    }
    archive_path = packages_root / "fixture.sagasmith-pack"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("package.sagasmith.json", json.dumps(package))
    archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (content_root / "index.json").write_text(
        json.dumps(
            {
                "schema": "sagasmith.current-content-packs.v1",
                "packages": [
                    {
                        "id": package["id"],
                        "version": package["version"],
                        "checksum": package["checksum"],
                        "archive_sha256": archive_sha256,
                        "archive_size": archive_path.stat().st_size,
                        "system_id": "dnd5e",
                        "kind": "addon",
                        "path": "packages/fixture.sagasmith-pack",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    lock = {
        "schema": OFFICIAL_EXPANSION_LOCK_SCHEMA,
        "source_repository": "fixture",
        "source_commit": "2" * 40,
        "packages": [
            {
                "id": package["id"],
                "version": package["version"],
                "checksum": package["checksum"],
                "archive_sha256": archive_sha256,
                "publication_id": "fixture2014",
                "title": "Fixture Supplement",
                "classification": "official_supplement",
                "editions": ["2014"],
                "content_summary": {"class": 1, "subclass": 1},
                "selection_ready": {"class": 1, "subclass": 1},
                "catalog_only": {},
            }
        ],
    }
    return lock, archive_path


def test_verifier_accounts_for_every_selection_ready_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, _archive_path = _fixture_library(tmp_path)
    monkeypatch.setattr(
        official_expansions,
        "validate_dnd_content_package",
        lambda package: package,
    )

    report = verify_official_expansion_library(tmp_path, lock=lock)

    assert report["verified"] is True
    assert report["coverage"] == {
        "packages": 1,
        "artifacts": 2,
        "selection_ready": 2,
        "catalog_only": 0,
        "content_summary": {"class": 1, "subclass": 1},
        "selection_ready_by_kind": {"class": 1, "subclass": 1},
        "catalog_only_by_kind": {},
    }
    assert report["rights"]["content_copied"] is False
    assert report["rights"]["license_granted"] is False


def test_resolver_returns_only_archives_that_completed_full_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, archive_path = _fixture_library(tmp_path)
    monkeypatch.setattr(
        official_expansions,
        "validate_dnd_content_package",
        lambda package: package,
    )

    resolved = resolve_official_expansion_archives(tmp_path, lock=lock)

    assert len(resolved) == 1
    assert resolved[0].id == "dnd5e.addon.fixture.official"
    assert resolved[0].editions == ("2014",)
    assert resolved[0].path == archive_path.resolve()
    assert resolved[0].archive_sha256 == lock["packages"][0]["archive_sha256"]


def test_verifier_rejects_archive_bytes_not_bound_by_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, archive_path = _fixture_library(tmp_path)
    monkeypatch.setattr(
        official_expansions,
        "validate_dnd_content_package",
        lambda package: package,
    )
    archive_path.write_bytes(archive_path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="archive checksum is stale"):
        verify_official_expansion_library(tmp_path, lock=lock)


def test_cli_verifier_does_not_open_the_runtime_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "verify_official_expansion_library",
        lambda path: {"verified": True, "library_root": str(path)},
    )
    monkeypatch.setattr(
        cli,
        "database",
        lambda: pytest.fail("content verification must not open the runtime database"),
    )

    code = cli.main(
        [
            "content",
            "verify-official-expansions",
            "--path",
            str(tmp_path),
            "--json",
        ]
    )

    response = json.loads(capsys.readouterr().out)
    assert code == 0
    assert response["data"] == {"verified": True, "library_root": str(tmp_path)}
