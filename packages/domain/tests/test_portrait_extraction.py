from __future__ import annotations

from pathlib import Path

import pytest

from sagasmith_dnd.portrait_extraction import PortraitExtractor, extract_actor_portrait

pymupdf = pytest.importorskip("pymupdf")


def _statblock(page, name: str) -> None:
    page.insert_text((40, 180), name, fontsize=18)
    page.insert_text((40, 205), "Armor Class 17")
    page.insert_text((40, 225), "Hit Points 99")
    page.insert_text((40, 245), "Challenge 8")


def test_text_only_statblock_is_not_published_as_a_portrait(tmp_path: Path) -> None:
    path = tmp_path / "text-only.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    _statblock(page, "Acererak")
    prose = " ".join(["rules text describes actions and saving throws"] * 28)
    page.insert_textbox(pymupdf.Rect(25, 30, 285, 760), prose, fontsize=9)
    page.insert_textbox(pymupdf.Rect(315, 30, 575, 760), prose, fontsize=9)
    document.save(path)
    document.close()

    assert (
        extract_actor_portrait(
            path,
            name="Acererak",
            page_number=1,
            minimum_confidence=0.24,
        )
        is None
    )
    with PortraitExtractor() as extractor:
        inspection = extractor.inspect(path, name="Acererak", page_number=1)
    assert inspection.portrait is None
    assert inspection.heading_found is True
    assert inspection.status in {"no_illustration", "no_visual_candidate"}


def test_portrait_inspection_distinguishes_missing_heading_and_invalid_page(
    tmp_path: Path,
) -> None:
    path = tmp_path / "diagnostics.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    _statblock(page, "Guard")
    document.save(path)
    document.close()

    with PortraitExtractor() as extractor:
        missing_heading = extractor.inspect(path, name="Mage", page_number=1)
        invalid_page = extractor.inspect(path, name="Guard", page_number=2)

    assert missing_heading.status == "heading_not_found"
    assert invalid_page.status == "page_out_of_range"


def test_full_column_illustration_trims_leading_prose_without_cutting_figure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "illustrated.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    _statblock(page, "Vajra Safahr")
    prose = " ".join(["introductory prose about the character"] * 10)
    page.insert_textbox(pymupdf.Rect(315, 30, 575, 150), prose, fontsize=9)
    for index, color in enumerate(((0.1, 0.2, 0.7), (0.8, 0.2, 0.2), (0.2, 0.7, 0.3))):
        page.draw_rect(
            pymupdf.Rect(330 + index * 18, 170 + index * 35, 555 - index * 12, 710),
            color=color,
            fill=color,
        )
    document.save(path)
    document.close()

    portrait = extract_actor_portrait(
        path,
        name="Vajra Safahr",
        page_number=1,
        minimum_confidence=0.1,
    )
    assert portrait is not None
    assert portrait.crop[1] >= 100
    assert portrait.crop[3] - portrait.crop[1] > 450
    assert portrait.method.startswith(("right_full", "opposite_column"))


def test_ocr_spaced_actor_heading_still_locates_source_portrait(tmp_path: Path) -> None:
    path = tmp_path / "spaced-heading.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    _statblock(page, "A z b a r a  J o s")
    for index, color in enumerate(((0.2, 0.3, 0.8), (0.8, 0.3, 0.2), (0.2, 0.8, 0.4))):
        page.draw_rect(
            pymupdf.Rect(330 + index * 15, 70 + index * 30, 555 - index * 10, 710),
            color=color,
            fill=color,
        )
    document.save(path)
    document.close()

    portrait = extract_actor_portrait(
        path,
        name="Azbara Jos",
        page_number=1,
        minimum_confidence=0.4,
    )
    assert portrait is not None


def test_extractor_reuses_words_and_one_full_page_render(tmp_path: Path) -> None:
    path = tmp_path / "cached-page.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    _statblock(page, "Guard")
    for index, color in enumerate(((0.2, 0.3, 0.8), (0.8, 0.3, 0.2), (0.2, 0.8, 0.4))):
        page.draw_rect(
            pymupdf.Rect(330 + index * 15, 70 + index * 30, 555 - index * 10, 710),
            color=color,
            fill=color,
        )
    document.save(path)
    document.close()

    with PortraitExtractor() as extractor:
        first = extractor.inspect(path, name="Guard", page_number=1).portrait
        second = extractor.inspect(path, name="Guard", page_number=1).portrait
        assert first is not None and second is not None
        assert first.content == second.content
        assert len(extractor._word_cache) == 1
        assert len(extractor._image_cache) == 1
