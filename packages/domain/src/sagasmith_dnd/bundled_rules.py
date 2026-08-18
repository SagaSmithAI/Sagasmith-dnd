"""Complete, bounded source catalog for the bundled 2014/2024 SRD corpus."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BundledRuleSource:
    """One indexable source assembled from a complete corpus partition."""

    source_key: str
    title: str
    content: str
    edition: str
    locale: str
    publication_id: str
    version: str
    checksum: str
    relative_paths: tuple[str, ...]

    def metadata(self) -> dict[str, Any]:
        return {
            "bundled": True,
            "complete_partition": True,
            "file_count": len(self.relative_paths),
            "relative_paths": list(self.relative_paths),
            "partition_checksum": self.checksum,
        }


def build_bundled_rule_sources(srd_root: str | Path) -> tuple[BundledRuleSource, ...]:
    """Partition every bundled SRD Markdown file into a small source catalog.

    The 2024 corpus already uses twenty page-range documents and retains those
    boundaries.  The much larger 2014 English and Chinese corpora use one source
    per top-level rules category, while retaining a heading and metadata entry
    for every leaf file.  No raw file is silently omitted by a cold-start limit.
    """

    root = Path(srd_root).expanduser().resolve()
    return _cached_bundled_rule_sources(str(root))


@lru_cache(maxsize=4)
def _cached_bundled_rule_sources(resolved_root: str) -> tuple[BundledRuleSource, ...]:
    root = Path(resolved_root)
    if not root.is_dir():
        raise FileNotFoundError(f"bundled SRD root is unavailable: {root}")
    sources: list[BundledRuleSource] = []
    sources.extend(
        _leaf_sources(
            root,
            corpus_dir="references",
            edition="2024",
            locale="en",
            publication_id="srd2024",
        )
    )
    for corpus_dir, locale in (
        ("references-2014-en", "en"),
        ("references-2014-zh", "zh"),
    ):
        corpus = root / corpus_dir
        if not corpus.is_dir():
            raise FileNotFoundError(f"bundled SRD corpus is unavailable: {corpus}")
        categories = sorted(path for path in corpus.iterdir() if path.is_dir())
        ungrouped = sorted(path for path in corpus.glob("*.md") if path.is_file())
        if ungrouped:
            sources.append(
                _aggregate_source(
                    root,
                    files=ungrouped,
                    source_slug="root",
                    title=f"D&D 5e SRD 2014 ({locale}) — Root",
                    edition="2014",
                    locale=locale,
                    publication_id="srd2014",
                    corpus_dir=corpus_dir,
                )
            )
        for category in categories:
            files = sorted(category.rglob("*.md"))
            if not files:
                continue
            sources.append(
                _aggregate_source(
                    root,
                    files=files,
                    source_slug=_slug(category.name),
                    title=(f"D&D 5e SRD 2014 ({locale}) — {category.name.replace('_', ' ')}"),
                    edition="2014",
                    locale=locale,
                    publication_id="srd2014",
                    corpus_dir=corpus_dir,
                )
            )
    _require_complete_partition(root, sources)
    return tuple(sources)


def bundled_rule_corpus_inventory(
    srd_root: str | Path,
    sources: tuple[BundledRuleSource, ...] | None = None,
) -> dict[str, Any]:
    """Return machine-checkable raw-file and source-partition coverage."""

    root = Path(srd_root).expanduser().resolve()
    catalog = sources or build_bundled_rule_sources(root)
    expected = _expected_files(root)
    covered = [relative for source in catalog for relative in source.relative_paths]
    by_corpus: dict[str, dict[str, Any]] = {}
    for corpus_dir, edition, locale in (
        ("references", "2024", "en"),
        ("references-2014-en", "2014", "en"),
        ("references-2014-zh", "2014", "zh"),
    ):
        prefix = f"{corpus_dir}/"
        paths = [path for path in expected if path.startswith(prefix)]
        by_corpus[f"{edition}:{locale}"] = {
            "edition": edition,
            "locale": locale,
            "files": len(paths),
            "sources": sum(
                1 for source in catalog if source.edition == edition and source.locale == locale
            ),
        }
    digest = hashlib.sha256("\n".join(expected).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "complete": sorted(covered) == expected and len(covered) == len(set(covered)),
        "files": len(expected),
        "sources": len(catalog),
        "path_manifest_checksum": digest,
        "corpora": by_corpus,
    }


def _leaf_sources(
    root: Path,
    *,
    corpus_dir: str,
    edition: str,
    locale: str,
    publication_id: str,
) -> list[BundledRuleSource]:
    corpus = root / corpus_dir
    if not corpus.is_dir():
        raise FileNotFoundError(f"bundled SRD corpus is unavailable: {corpus}")
    return [
        _aggregate_source(
            root,
            files=[path],
            source_slug=_slug(path.stem),
            title=path.stem,
            edition=edition,
            locale=locale,
            publication_id=publication_id,
            corpus_dir=corpus_dir,
        )
        for path in sorted(corpus.glob("*.md"))
    ]


def _aggregate_source(
    root: Path,
    *,
    files: list[Path],
    source_slug: str,
    title: str,
    edition: str,
    locale: str,
    publication_id: str,
    corpus_dir: str,
) -> BundledRuleSource:
    relative_paths = tuple(path.relative_to(root).as_posix() for path in files)
    sections = []
    for path, relative in zip(files, relative_paths, strict=True):
        text = path.read_text(encoding="utf-8-sig").strip()
        sections.append(f"# Bundled source file: {relative}\n\n{text}\n")
    content = "\n\n".join(sections).strip() + "\n"
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return BundledRuleSource(
        source_key=f"bundled:{publication_id}:{locale}:{source_slug}",
        title=title,
        content=content,
        edition=edition,
        locale=locale,
        publication_id=publication_id,
        version=f"bundled-{checksum[:16]}",
        checksum=checksum,
        relative_paths=relative_paths,
    )


def _expected_files(root: Path) -> list[str]:
    expected = []
    for corpus_dir in ("references", "references-2014-en", "references-2014-zh"):
        corpus = root / corpus_dir
        if not corpus.is_dir():
            raise FileNotFoundError(f"bundled SRD corpus is unavailable: {corpus}")
        expected.extend(path.relative_to(root).as_posix() for path in sorted(corpus.rglob("*.md")))
    return sorted(expected)


def _require_complete_partition(
    root: Path,
    sources: list[BundledRuleSource],
) -> None:
    expected = _expected_files(root)
    covered = [relative for source in sources for relative in source.relative_paths]
    duplicates = sorted(relative for relative, count in Counter(covered).items() if count > 1)
    missing = sorted(set(expected) - set(covered))
    unexpected = sorted(set(covered) - set(expected))
    if missing or unexpected or duplicates:
        raise RuntimeError(
            "bundled SRD partition is incomplete: "
            f"missing={missing[:10]}, unexpected={unexpected[:10]}, "
            f"duplicates={duplicates[:10]}"
        )


def _slug(value: str) -> str:
    normalized = "".join(
        character.casefold() if character.isalnum() else "-" for character in value
    )
    return "-".join(part for part in normalized.split("-") if part) or "source"


__all__ = [
    "BundledRuleSource",
    "build_bundled_rule_sources",
    "bundled_rule_corpus_inventory",
]
