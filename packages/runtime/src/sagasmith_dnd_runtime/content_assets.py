"""Application file adapters extracted from Domain content_packages."""
from __future__ import annotations

import copy
import hashlib
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from sagasmith_core.content_pack import (
    blob_descriptor,
)
from sagasmith_dnd.content_packages import _rebuild_package, _slug

if TYPE_CHECKING:
    from sagasmith_dnd_runtime.portrait_extraction import ExtractedPortrait, PortraitExtractor


def _portrait_extractor_type() -> type[PortraitExtractor]:
    """Load image support only for an explicit portrait extraction request."""

    try:
        from sagasmith_dnd_runtime.portrait_extraction import PortraitExtractor
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != "PIL":
            raise
        raise RuntimeError(
            "Actor portrait extraction requires `pip install \"sagasmith-dnd-runtime[images]\"`"
        ) from exc
    return PortraitExtractor


def attach_source_originals(
    package: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    source_paths: Mapping[str, str | Path],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Attach licensed original documents to matching source bundles."""

    assets = copy.deepcopy(list(package["assets"]))
    sources = copy.deepcopy(list(package["sources"]))
    next_blobs = {str(key): bytes(value) for key, value in blobs.items()}
    license = str(package["metadata"].get("license") or "private")
    attribution = str(package["metadata"].get("attribution") or "User supplied source")
    for source in sources:
        path_value = source_paths.get(str(source["source_key"]))
        if path_value is None:
            continue
        path = Path(path_value)
        content = path.read_bytes()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        asset = blob_descriptor(
            asset_key=f"source.{_slug(str(source['source_key']))}.original",
            kind="original_document",
            name=path.name,
            media_type=media_type,
            content=content,
            license=license,
            attribution=attribution,
            metadata={"source_key": source["source_key"]},
        )
        assets.append(asset)
        next_blobs.setdefault(asset["checksum"], content)
        source["original_asset_keys"] = [
            *source["original_asset_keys"],
            asset["asset_key"],
        ]
    return _rebuild_package(package, sources=sources, assets=assets), next_blobs


def attach_auxiliary_assets(
    package: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    entries: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Attach source-distributed maps, handouts, and reference documents.

    Auxiliary assets are intentionally distinct from ``original_document``:
    the latter is the exact document normalized into one source bundle, while
    these files accompany the package without pretending that their contents
    were indexed or used as mechanical evidence.
    """

    assets = copy.deepcopy(list(package["assets"]))
    next_blobs = {str(key): bytes(value) for key, value in blobs.items()}
    license = str(package["metadata"].get("license") or "private")
    attribution = str(package["metadata"].get("attribution") or "User supplied source")
    existing_by_key = {str(asset["asset_key"]): asset for asset in assets}
    for index, raw_entry in enumerate(entries):
        entry = dict(raw_entry)
        allowed = {
            "path",
            "kind",
            "logical_path",
            "name",
            "metadata",
            "source_refs",
        }
        unexpected = set(entry) - allowed
        if unexpected:
            raise ValueError(f"auxiliary asset {index} has unexpected fields: {sorted(unexpected)}")
        path_value = entry.get("path")
        kind = " ".join(str(entry.get("kind") or "").split())
        if path_value is None or not kind:
            raise ValueError(f"auxiliary asset {index} requires path and kind")
        path = Path(path_value)
        if not path.is_file():
            raise FileNotFoundError(path)
        logical_path = str(entry.get("logical_path") or path.name).replace("\\", "/").strip("/")
        if not logical_path or ".." in Path(logical_path).parts:
            raise ValueError(f"auxiliary asset {index} has an invalid logical_path")
        content = path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        asset_key = f"auxiliary.{_slug(logical_path)}.{checksum[:16]}"
        descriptor = blob_descriptor(
            asset_key=asset_key,
            kind=kind,
            name=str(entry.get("name") or path.name),
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            content=content,
            license=license,
            attribution=attribution,
            source_refs=copy.deepcopy(list(entry.get("source_refs") or [])),
            metadata={
                "logical_path": logical_path,
                "relationship": "package_auxiliary",
                **copy.deepcopy(dict(entry.get("metadata") or {})),
            },
        )
        existing = existing_by_key.get(asset_key)
        if existing is not None:
            if existing != descriptor or next_blobs.get(checksum) != content:
                raise ValueError(f"conflicting auxiliary asset key: {asset_key}")
            continue
        assets.append(descriptor)
        existing_by_key[asset_key] = descriptor
        next_blobs.setdefault(descriptor["checksum"], content)
    return (
        _rebuild_package(package, assets=assets),
        next_blobs,
    )


def _portrait_sources(actor: Mapping[str, Any]) -> list[tuple[str, int]]:
    refs = dict(actor.get("provenance") or {}).get("source_refs") or []
    result = []
    for ref in refs:
        if isinstance(ref, Mapping) and isinstance(ref.get("page"), int) and int(ref["page"]) > 0:
            result.append((str(ref["source_key"]), int(ref["page"])))
    return list(dict.fromkeys(result))


def _portrait_cache_key(actor: Mapping[str, Any]) -> str:
    """Reuse a crop only when actor identity and exact evidence refs agree."""

    refs = dict(actor.get("provenance") or {}).get("source_refs") or []
    evidence = "|".join(
        f"{ref['source_key']}:{ref.get('chunk_key', '')}:{int(ref['page'])}"
        for ref in refs
        if isinstance(ref, Mapping) and isinstance(ref.get("page"), int) and int(ref["page"]) > 0
    )
    return f"{_slug(str(actor['name']))}|{evidence}"


def attach_actor_portraits(
    package: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    source_paths: Mapping[str, str | Path],
    *,
    portrait_library: Mapping[str, ExtractedPortrait] | None = None,
    portrait_reviews: Mapping[str, Mapping[str, Any]] | None = None,
    minimum_confidence: float = 0.18,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any], dict[str, ExtractedPortrait]]:
    """Attach audited art to static actors and source statblock card templates."""

    assets = copy.deepcopy(list(package["assets"]))
    actors = copy.deepcopy(list(package["actors"]))
    content = copy.deepcopy(dict(package["content"]))
    artifacts = list(content.get("artifacts") or [])
    subjects = [
        {
            "subject_type": "actor",
            "subject_id": str(actor["id"]),
            "name": str(actor["name"]),
            "source_refs": list(dict(actor.get("provenance") or {}).get("source_refs") or []),
            "target": actor,
        }
        for actor in actors
    ]
    for artifact in artifacts:
        if not isinstance(artifact, dict) or str(artifact.get("kind") or "") != "statblock":
            continue
        card = artifact.get("card")
        if not isinstance(card, dict) or not str(card.get("name") or "").strip():
            continue
        subjects.append(
            {
                "subject_type": "statblock_card",
                "subject_id": str(artifact.get("id") or ""),
                "name": str(card["name"]),
                "source_refs": list(artifact.get("source_refs") or []),
                "target": card,
            }
        )
    next_blobs = {str(key): bytes(value) for key, value in blobs.items()}
    library = dict(portrait_library or {})
    license = str(package["metadata"].get("license") or "private")
    attribution = str(package["metadata"].get("attribution") or "User supplied source")
    extracted = 0
    reused = 0
    missing = []
    review_required = []
    illustration_absent = []
    reviewed = []
    reviews = {str(key): dict(item) for key, item in dict(portrait_reviews or {}).items()}
    consumed_review_keys: set[str] = set()
    image_refs_by_cache: dict[str, dict[str, str]] = {}
    with _portrait_extractor_type()() as extractor:
        for subject in subjects:
            target = subject["target"]
            if target.get("image") is not None:
                continue
            review_key = (
                f"{package['id']}|{subject['subject_type']}|{subject['subject_id']}"
            )
            raw_review = reviews.get(review_key)
            active_review: dict[str, Any] | None = None
            normalized_name = _slug(subject["name"])
            evidence_subject = {
                "name": subject["name"],
                "provenance": {"source_refs": subject["source_refs"]},
            }
            cache_key = _portrait_cache_key(evidence_subject)
            sources = _portrait_sources(evidence_subject)
            if raw_review is not None:
                decision = str(raw_review.get("decision") or "").strip()
                reviewer = " ".join(str(raw_review.get("reviewer") or "").split())
                note = " ".join(str(raw_review.get("note") or "").split())
                if not reviewer:
                    raise ValueError(f"portrait review {review_key} requires reviewer")
                allowed = (
                    {"decision", "reviewer", "note"}
                    if decision == "illustration_absent"
                    else {"decision", "source_key", "page", "crop", "reviewer", "note"}
                )
                unknown = set(raw_review) - allowed
                if unknown:
                    raise ValueError(
                        f"portrait review {review_key} has unsupported fields: {sorted(unknown)}"
                    )
                consumed_review_keys.add(review_key)
                active_review = {
                    "review_key": review_key,
                    "decision": decision,
                    "reviewer": reviewer,
                    "note": note,
                }
                if decision == "illustration_absent":
                    entry = {
                        "review_key": review_key,
                        "subject_type": subject["subject_type"],
                        "subject_id": subject["subject_id"],
                        "name": subject["name"],
                        "sources": [
                            {"source_key": source_key, "page": page}
                            for source_key, page in sources
                        ],
                        "diagnostics": [],
                        "reason": "reviewer confirmed that the source has no usable portrait",
                        "review": active_review,
                    }
                    illustration_absent.append(entry)
                    missing.append(
                        {
                            "subject_type": subject["subject_type"],
                            "subject_id": subject["subject_id"],
                            "name": subject["name"],
                            "reason": "reviewed illustration_absent",
                        }
                    )
                    reviewed.append(active_review)
                    continue
                if decision != "crop":
                    raise ValueError(
                        f"portrait review {review_key} decision must be crop or "
                        "illustration_absent"
                    )
                source_key = str(raw_review.get("source_key") or "")
                page = raw_review.get("page")
                crop = raw_review.get("crop")
                if (
                    not isinstance(page, int)
                    or isinstance(page, bool)
                    or (source_key, page) not in sources
                    or source_key not in source_paths
                    or not isinstance(crop, list)
                    or len(crop) != 4
                ):
                    raise ValueError(
                        f"portrait review {review_key} must bind an exact cited source page "
                        "and four-value crop"
                    )
                portrait = extractor.extract_reviewed_crop(
                    source_paths[source_key],
                    page_number=page,
                    crop=tuple(crop),
                )
                library[cache_key] = portrait
                extracted += 1
                active_review.update(
                    {
                        "source_key": source_key,
                        "page": page,
                        "crop": [float(value) for value in crop],
                    }
                )
                reviewed.append(active_review)
            else:
                portrait = None
            existing_ref = image_refs_by_cache.get(cache_key)
            if raw_review is None and existing_ref is not None:
                target["image"] = {
                    "asset_key": existing_ref["asset_key"],
                    "alt": f"{subject['name']} portrait",
                }
                reused += 1
                continue
            if raw_review is None:
                portrait = library.get(cache_key)
            if portrait is None:
                inspections = [
                    extractor.inspect(
                        source_paths[source[0]],
                        name=subject["name"],
                        page_number=source[1],
                        minimum_confidence=minimum_confidence,
                    )
                    for source in sources
                    if source[0] in source_paths
                ]
                candidates = [
                    inspection.portrait
                    for inspection in inspections
                    if inspection.portrait is not None
                ]
                portrait = max(candidates, key=lambda item: item.confidence, default=None)
                if portrait is not None:
                    library.setdefault(cache_key, portrait)
                    extracted += 1
                else:
                    diagnostics = [
                        {
                            "status": inspection.status,
                            "heading_found": inspection.heading_found,
                            "candidate_count": inspection.candidate_count,
                            "best_confidence": inspection.best_confidence,
                        }
                        for inspection in inspections
                    ]
                    entry = {
                        "review_key": review_key,
                        "subject_type": subject["subject_type"],
                        "subject_id": subject["subject_id"],
                        "name": subject["name"],
                        "sources": [
                            {"source_key": source_key, "page": page} for source_key, page in sources
                        ],
                        "diagnostics": diagnostics,
                    }
                    entry["reason"] = "source image extraction requires review"
                    review_required.append(entry)
            elif portrait is not None and raw_review is None:
                reused += 1
            if portrait is None:
                missing.append(
                    {
                        "subject_type": subject["subject_type"],
                        "subject_id": subject["subject_id"],
                        "name": subject["name"],
                        "reason": (
                            "source image extraction requires review"
                            if any(
                                item["subject_type"] == subject["subject_type"]
                                and item["subject_id"] == subject["subject_id"]
                                for item in review_required
                            )
                            else "source page has no reliable actor illustration"
                        ),
                    }
                )
                continue
            refs = subject["source_refs"]
            asset = blob_descriptor(
                asset_key=(
                    f"{subject['subject_type']}.{_slug(subject['subject_id'])}.image"
                ),
                kind="actor_image",
                name=f"{normalized_name}.webp",
                media_type=portrait.media_type,
                content=portrait.content,
                license=license,
                attribution=attribution,
                source_refs=refs,
                metadata={
                    "subject_type": subject["subject_type"],
                    "subject_id": subject["subject_id"],
                    "extraction": {
                        "method": portrait.method,
                        "page": portrait.page,
                        "crop": list(portrait.crop),
                        "confidence": portrait.confidence,
                    },
                    **({"review": active_review} if active_review is not None else {}),
                },
            )
            assets.append(asset)
            next_blobs.setdefault(asset["checksum"], portrait.content)
            image_refs_by_cache[cache_key] = {
                "asset_key": asset["asset_key"],
            }
            target["image"] = {
                "asset_key": asset["asset_key"],
                "alt": f"{subject['name']} portrait",
            }
    unmatched_reviews = sorted(set(reviews) - consumed_review_keys)
    if unmatched_reviews:
        raise ValueError(
            "portrait reviews matched no package subject: " + ", ".join(unmatched_reviews)
        )
    next_package = _rebuild_package(
        package,
        assets=assets,
        actors=actors,
        content=content,
    )
    return (
        next_package,
        next_blobs,
        {
            "actors": len(actors),
            "images": sum(actor["image"] is not None for actor in actors),
            "statblock_cards": sum(
                subject["subject_type"] == "statblock_card" for subject in subjects
            ),
            "statblock_card_images": sum(
                subject["subject_type"] == "statblock_card"
                and subject["target"].get("image") is not None
                for subject in subjects
            ),
            "subjects": len(subjects),
            "subject_images": sum(
                subject["target"].get("image") is not None for subject in subjects
            ),
            "extracted": extracted,
            "reused": reused,
            "missing": missing,
            "review_required": review_required,
            "illustration_absent": illustration_absent,
            "reviewed": reviewed,
            "complete": not review_required,
        },
        library,
    )
