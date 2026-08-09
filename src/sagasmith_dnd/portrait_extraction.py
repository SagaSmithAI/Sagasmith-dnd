"""Source-backed actor portrait extraction from illustrated PDF pages."""

from __future__ import annotations

import io
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat


@dataclass(frozen=True)
class ExtractedPortrait:
    content: bytes
    media_type: str
    page: int
    crop: tuple[float, float, float, float]
    confidence: float
    method: str


@dataclass(frozen=True)
class PortraitInspection:
    portrait: ExtractedPortrait | None
    status: str
    heading_found: bool
    candidate_count: int
    best_confidence: float | None


def _pymupdf() -> Any:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - exercised without the optional extra
        raise RuntimeError("portrait extraction requires sagasmith-dnd[images]") from exc
    return pymupdf


def _intersection_area(first: Any, second: Any) -> float:
    left = max(float(first.x0), float(second.x0))
    top = max(float(first.y0), float(second.y0))
    right = min(float(first.x1), float(second.x1))
    bottom = min(float(first.y1), float(second.y1))
    return max(0.0, right - left) * max(0.0, bottom - top)


def _compact_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _identity_heading_matches(page: Any, name: str, *, words: list[Any] | None = None) -> list[Any]:
    """Find exact or OCR-space-damaged identity headings on one page."""

    direct = list(page.search_for(name))
    if direct:
        return direct
    pymupdf = _pymupdf()
    target = _compact_identity(name)
    if len(target) < 3:
        return []
    words = list(page.get_text("words")) if words is None else words
    matches = []
    for start, first in enumerate(words):
        line = (int(first[5]), int(first[6]))
        combined = ""
        rect = None
        for raw_word in words[start : start + min(16, len(target) + 4)]:
            if (int(raw_word[5]), int(raw_word[6])) != line:
                break
            token = _compact_identity(str(raw_word[4]))
            if not token:
                continue
            combined += token
            word_rect = pymupdf.Rect(raw_word[:4])
            rect = word_rect if rect is None else rect | word_rect
            if combined == target:
                matches.append(rect)
                break
            if len(combined) >= len(target) or not target.startswith(combined):
                break
    return matches


def _statblock_heading(page: Any, name: str, *, words: list[Any] | None = None) -> Any | None:
    matches = _identity_heading_matches(page, name, words=words)
    if not matches:
        return None
    armor = list(page.search_for("Armor Class"))
    challenge = list(page.search_for("Challenge"))
    scored = []
    for match in matches:
        mechanics = [
            item
            for item in [*armor, *challenge]
            if item.y0 >= match.y0 - 4
            and item.y0 <= match.y0 + 280
            and abs(item.x0 - match.x0) <= page.rect.width * 0.34
        ]
        if not mechanics:
            continue
        nearest = min(item.y0 - match.y0 for item in mechanics)
        scored.append((nearest, -match.y0, match))
    if scored:
        return min(scored, key=lambda item: (item[0], item[1]))[2]
    return max(matches, key=lambda item: item.y0)


def _candidate_crops(page: Any, heading: Any | None) -> list[tuple[str, Any]]:
    pymupdf = _pymupdf()
    bounds = page.rect
    margin_x = max(8.0, bounds.width * 0.035)
    margin_y = max(8.0, bounds.height * 0.045)
    gutter = bounds.width * 0.025
    middle = bounds.width / 2
    columns = (
        (margin_x, middle - gutter),
        (middle + gutter, bounds.width - margin_x),
    )
    result: list[tuple[str, Any]] = []
    if heading is not None:
        column = columns[0] if heading.x0 < middle else columns[1]
        above = pymupdf.Rect(column[0], margin_y, column[1], heading.y0 - 5)
        below = pymupdf.Rect(
            column[0],
            min(bounds.height - margin_y, heading.y1 + bounds.height * 0.22),
            column[1],
            bounds.height - margin_y,
        )
        result.extend(
            [
                ("same_column_above_statblock", above),
                ("same_column_below_statblock", below),
            ]
        )
        other = columns[1] if column == columns[0] else columns[0]
        result.append(
            (
                "opposite_column",
                pymupdf.Rect(other[0], margin_y, other[1], bounds.height - margin_y),
            )
        )
    result.extend(
        [
            (
                "left_full",
                pymupdf.Rect(margin_x, margin_y, middle - gutter, bounds.height - margin_y),
            ),
            (
                "right_full",
                pymupdf.Rect(
                    middle + gutter, margin_y, bounds.width - margin_x, bounds.height - margin_y
                ),
            ),
            (
                "left_upper",
                pymupdf.Rect(margin_x, margin_y, middle - gutter, bounds.height * 0.58),
            ),
            (
                "right_upper",
                pymupdf.Rect(
                    middle + gutter, margin_y, bounds.width - margin_x, bounds.height * 0.58
                ),
            ),
            (
                "left_lower",
                pymupdf.Rect(
                    margin_x, bounds.height * 0.42, middle - gutter, bounds.height - margin_y
                ),
            ),
            (
                "right_lower",
                pymupdf.Rect(
                    middle + gutter,
                    bounds.height * 0.42,
                    bounds.width - margin_x,
                    bounds.height - margin_y,
                ),
            ),
        ]
    )
    return [
        (method, rect & bounds)
        for method, rect in result
        if rect.width >= bounds.width * 0.25 and rect.height >= bounds.height * 0.16
    ]


def _trim_leading_text(page: Any, rect: Any, *, words: list[Any] | None = None) -> tuple[Any, bool]:
    """Remove a dense prose band above an otherwise illustrated column."""

    pymupdf = _pymupdf()
    upper_limit = rect.y0 + rect.height * 0.38
    page_words = list(page.get_text("words")) if words is None else words
    upper_words = [
        pymupdf.Rect(word[:4])
        for word in page_words
        if pymupdf.Rect(word[:4]).intersects(rect) and float(word[1]) < upper_limit
    ]
    if len(upper_words) < 18:
        return rect, False
    cutoff = max(float(word.y1) for word in upper_words) + max(6.0, rect.height * 0.012)
    trimmed = pymupdf.Rect(rect.x0, cutoff, rect.x1, rect.y1)
    if trimmed.height < page.rect.height * 0.28:
        return rect, False
    return trimmed, True


def _visual_score(
    page: Any,
    rect: Any,
    *,
    heading: Any | None,
    words: list[Any] | None = None,
    page_image: Image.Image | None = None,
) -> tuple[float, Image.Image, int]:
    pymupdf = _pymupdf()
    if page_image is None:
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False)
        page_image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
    scale_x = page_image.width / float(page.rect.width)
    scale_y = page_image.height / float(page.rect.height)
    crop = (
        max(0, round((float(rect.x0) - float(page.rect.x0)) * scale_x)),
        max(0, round((float(rect.y0) - float(page.rect.y0)) * scale_y)),
        min(page_image.width, round((float(rect.x1) - float(page.rect.x0)) * scale_x)),
        min(page_image.height, round((float(rect.y1) - float(page.rect.y0)) * scale_y)),
    )
    image = page_image.crop(crop)
    grayscale = image.convert("L")
    stats = ImageStat.Stat(grayscale)
    variance = float(stats.var[0])
    edges = ImageStat.Stat(grayscale.filter(ImageFilter.FIND_EDGES)).mean[0]
    color_stats = ImageStat.Stat(image)
    colorfulness = sum(float(value) for value in color_stats.var) / 3
    page_words = list(page.get_text("words")) if words is None else words
    intersecting_words = [
        pymupdf.Rect(word[:4]) for word in page_words if pymupdf.Rect(word[:4]).intersects(rect)
    ]
    text_area = sum(_intersection_area(rect, word) for word in intersecting_words)
    area = max(1.0, rect.width * rect.height)
    text_ratio = min(1.0, text_area / area)
    proximity = 0.0
    if heading is not None:
        center_x = (rect.x0 + rect.x1) / 2
        center_y = (rect.y0 + rect.y1) / 2
        distance = math.hypot(center_x - heading.x0, center_y - heading.y0)
        proximity = max(0.0, 1.0 - distance / math.hypot(page.rect.width, page.rect.height))
    visual = min(1.0, variance / 1800) * 0.45
    visual += min(1.0, edges / 35) * 0.2
    visual += min(1.0, colorfulness / 2400) * 0.15
    visual += proximity * 0.2
    word_density = len(intersecting_words) / max(1.0, area / 10_000)
    score = visual - text_ratio * 2.4 - min(0.8, word_density / 18)
    return score, image, len(intersecting_words)


class PortraitExtractor:
    """Reuse open PDF handles while extracting a package's actor portraits."""

    def __init__(self) -> None:
        self._documents: dict[Path, Any] = {}
        self._word_cache: OrderedDict[tuple[Path, int], list[Any]] = OrderedDict()
        self._image_cache: OrderedDict[tuple[Path, int], Image.Image] = OrderedDict()

    def _page_words(self, source: Path, page_number: int, page: Any) -> list[Any]:
        key = (source, page_number)
        cached = self._word_cache.get(key)
        if cached is not None:
            self._word_cache.move_to_end(key)
            return cached
        words = list(page.get_text("words"))
        self._word_cache[key] = words
        while len(self._word_cache) > 32:
            self._word_cache.popitem(last=False)
        return words

    def _page_image(self, source: Path, page_number: int, page: Any) -> Image.Image:
        key = (source, page_number)
        cached = self._image_cache.get(key)
        if cached is not None:
            self._image_cache.move_to_end(key)
            return cached
        pymupdf = _pymupdf()
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False)
        image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
        self._image_cache[key] = image
        while len(self._image_cache) > 8:
            _evicted_key, evicted_image = self._image_cache.popitem(last=False)
            evicted_image.close()
        return image

    def close(self) -> None:
        for image in self._image_cache.values():
            image.close()
        self._image_cache.clear()
        self._word_cache.clear()
        for document in self._documents.values():
            document.close()
        self._documents.clear()

    def __enter__(self) -> PortraitExtractor:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def extract(
        self,
        source_path: str | Path,
        *,
        name: str,
        page_number: int,
        minimum_confidence: float = 0.18,
    ) -> ExtractedPortrait | None:
        """Return the extracted portrait, retaining the compact legacy API."""

        return self.inspect(
            source_path,
            name=name,
            page_number=page_number,
            minimum_confidence=minimum_confidence,
        ).portrait

    def inspect(
        self,
        source_path: str | Path,
        *,
        name: str,
        page_number: int,
        minimum_confidence: float = 0.18,
    ) -> PortraitInspection:
        """Extract the best source-page illustration near an actor statblock.

        The method relies on the PDF text layer to find the actor's statblock and
        scores nearby low-text, visually complex regions.  It does not invent
        art; an uncertain page produces ``None`` for the build audit.
        """

        if page_number < 1:
            raise ValueError("page_number must be positive")
        pymupdf = _pymupdf()
        source = Path(source_path).expanduser().resolve()
        document = self._documents.get(source)
        if document is None:
            document = pymupdf.open(source)
            self._documents[source] = document
        if page_number > document.page_count:
            return PortraitInspection(None, "page_out_of_range", False, 0, None)
        page = document[page_number - 1]
        words = self._page_words(source, page_number, page)
        heading = _statblock_heading(page, name, words=words)
        if heading is None:
            return PortraitInspection(None, "heading_not_found", False, 0, None)
        page_image = self._page_image(source, page_number, page)
        candidates = []
        for method, rect in _candidate_crops(page, heading):
            trimmed_rect, trimmed = _trim_leading_text(page, rect, words=words)
            score, image, word_count = _visual_score(
                page,
                trimmed_rect,
                heading=heading,
                words=words,
                page_image=page_image,
            )
            if word_count > 40:
                continue
            if trimmed:
                method = f"{method}_prose_trimmed"
                score += 0.04
            if method.startswith(("left_full", "right_full", "opposite_column")):
                score += 0.12
            candidates.append((score, method, trimmed_rect, image))
        if not candidates:
            return PortraitInspection(None, "no_visual_candidate", True, 0, None)
        score, method, rect, image = max(candidates, key=lambda item: item[0])
        # A caller may ask for a stricter threshold, but never weaken the
        # library-wide floor: mostly blank or text-only regions can otherwise
        # receive a small proximity/edge score and masquerade as portraits.
        if score < max(0.4, minimum_confidence):
            status = "review_required_low_confidence" if score >= 0.30 else "no_illustration"
            return PortraitInspection(None, status, True, len(candidates), score)
        width, height = image.size
        if width < 128 or height < 128:
            return PortraitInspection(
                None,
                "review_required_small_candidate",
                True,
                len(candidates),
                score,
            )
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=88, method=6)
        confidence = max(0.0, min(1.0, score))
        return PortraitInspection(
            portrait=ExtractedPortrait(
                content=output.getvalue(),
                media_type="image/webp",
                page=page_number,
                crop=(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                confidence=confidence,
                method=method,
            ),
            status="extracted",
            heading_found=True,
            candidate_count=len(candidates),
            best_confidence=confidence,
        )

    def extract_reviewed_crop(
        self,
        source_path: str | Path,
        *,
        page_number: int,
        crop: tuple[float, float, float, float],
    ) -> ExtractedPortrait:
        """Extract an exact page-space crop approved by an Agent or human reviewer."""

        if page_number < 1:
            raise ValueError("page_number must be positive")
        if len(crop) != 4 or any(not isinstance(value, (int, float)) for value in crop):
            raise ValueError("reviewed portrait crop must contain four numbers")
        pymupdf = _pymupdf()
        source = Path(source_path).expanduser().resolve()
        document = self._documents.get(source)
        if document is None:
            document = pymupdf.open(source)
            self._documents[source] = document
        if page_number > document.page_count:
            raise ValueError("reviewed portrait page is outside the source document")
        page = document[page_number - 1]
        requested = pymupdf.Rect(*[float(value) for value in crop])
        bounded = requested & page.rect
        if bounded != requested or bounded.width <= 0 or bounded.height <= 0:
            raise ValueError("reviewed portrait crop must stay inside the source page")
        _score, image, _word_count = _visual_score(
            page,
            bounded,
            heading=None,
            words=self._page_words(source, page_number, page),
            page_image=self._page_image(source, page_number, page),
        )
        if image.width < 128 or image.height < 128:
            raise ValueError("reviewed portrait crop must render at least 128 by 128 pixels")
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=88, method=6)
        return ExtractedPortrait(
            content=output.getvalue(),
            media_type="image/webp",
            page=page_number,
            crop=tuple(float(value) for value in crop),
            confidence=1.0,
            method="agent_reviewed_crop",
        )


def extract_actor_portrait(
    source_path: str | Path,
    *,
    name: str,
    page_number: int,
    minimum_confidence: float = 0.18,
) -> ExtractedPortrait | None:
    """Extract one portrait, opening and closing its source document."""

    with PortraitExtractor() as extractor:
        return extractor.extract(
            source_path,
            name=name,
            page_number=page_number,
            minimum_confidence=minimum_confidence,
        )


__all__ = [
    "ExtractedPortrait",
    "PortraitInspection",
    "PortraitExtractor",
    "extract_actor_portrait",
]
