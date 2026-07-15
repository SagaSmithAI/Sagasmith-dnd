from __future__ import annotations

from sagasmith_core.modules import MarkdownModuleParser

from sagasmith_dnd.module_profile import DndModuleProfile


def test_dnd_scene_parser_matches_agent_hierarchy_behavior() -> None:
    parsed = MarkdownModuleParser(profile=DndModuleProfile()).parse(
        "# Arrival\n"
        "Chapter overview.\n"
        "## Gate\n"
        "Description.\n"
        "### 遭遇\n"
        "Guards approach.\n"
        "#### A1. Cellar\n"
        "Treasure waits below.\n"
        "## 酒馆\n"
        "\n"
        "## Tavern\n"
        "Talk to the innkeeper.\n"
    )

    scenes = list(parsed[0].scenes)
    assert [scene.title for scene in scenes] == [
        "Arrival",
        "Gate",
        "酒馆 Tavern",
    ]
    assert scenes[0].metadata["tags"] == ["exploration"]
    assert scenes[1].metadata["scene_level"] == 2
    assert scenes[1].metadata["tags"] == ["exploration", "combat"]
    assert scenes[1].metadata["subsections"] == [
        {"title": "遭遇", "line": 5, "type": "section"},
        {"title": "A1. Cellar", "line": 7, "type": "room"},
    ]
    assert scenes[1].metadata["headings"] == ["遭遇", "A1. Cellar"]


def test_dnd_scene_parser_promotes_h3_when_it_dominates_h2() -> None:
    content = (
        "# Chapter\n"
        "## Running the Chapter\n"
        "Overview.\n"
        "### One\nText.\n"
        "### Two\nText.\n"
        "### Three\nText.\n"
        "### Four\nText.\n"
        "### Five\nText.\n"
    )

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    assert [scene.title for scene in scenes] == [
        "Chapter",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
    ]
    assert all(scene.metadata["scene_level"] == 3 for scene in scenes)


def test_room_dimensions_are_bound_to_their_own_heading_content() -> None:
    content = (
        "# Keep\n## Cellars\n"
        "#### A1. Guard Room\nThis chamber is 30 by 20 feet.\n"
        "#### A2. Shrine\nThis chamber is 15 by 10 feet.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "Cellars"
    )
    locations = scene.metadata["spatial"]["locations"]
    assert locations[0]["dimensions_ft"] == {"width": 30, "height": 20}
    assert locations[1]["dimensions_ft"] == {"width": 15, "height": 10}
