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


def test_dnd_profile_parses_generated_runtime_manifest() -> None:
    content = """<!-- sagasmith-runtime-manifest
{
  "schema_version": 1,
  "module_key": "keep-on-borderlands",
  "entities": [{"id": "npc:keeper", "kind": "npc", "name": "Keeper"}],
  "secrets": [{"id": "secret:keeper-oath", "initial_knowers": ["npc:keeper"]}],
  "clues": [{"id": "clue:broken-seal", "trigger": "inspect the gate"}],
  "plot_nodes": [{"id": "plot:open-gate", "trigger": "repair the seal", "consequences": []}],
  "foreshadowing": [{"id": "foreshadow:red-ravens"}],
  "branches": [{"id": "branch:parley", "trigger": "offer terms", "consequences": []}]
}
-->
# Chapter
## Arrival
The party arrives.
"""

    metadata = MarkdownModuleParser(profile=DndModuleProfile()).document_metadata(content)

    assert metadata["runtime_manifest"]["module_key"] == "keep-on-borderlands"
    assert metadata["runtime_manifest_errors"] == []


def test_dnd_profile_rejects_duplicate_or_unroutable_manifest_entries() -> None:
    content = """<!-- sagasmith-runtime-manifest
{
  "schema_version": 1,
  "module_key": "Bad Key",
  "entities": [{"id": "npc:keeper"}],
  "secrets": [{"id": "npc:keeper", "initial_knowers": "everyone"}],
  "clues": [{"id": "clue:seal"}]
}
-->
# Chapter
## Arrival
Text.
"""

    metadata = MarkdownModuleParser(profile=DndModuleProfile()).document_metadata(content)

    assert "runtime manifest module_key must be a stable lowercase id" in metadata[
        "runtime_manifest_errors"
    ]
    assert "runtime manifest contains duplicate id: npc:keeper" in metadata[
        "runtime_manifest_errors"
    ]
    assert "runtime manifest secrets[0].initial_knowers must be a list" in metadata[
        "runtime_manifest_errors"
    ]
    assert "runtime manifest clues[0].trigger is required" in metadata[
        "runtime_manifest_errors"
    ]


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


def test_deep_numbered_adventure_areas_populate_scene_atlas() -> None:
    content = (
        "# Part 1\n## CRAGMAW HIDEOUT\n"
        "##### 1. CAVE MOUTH\nA stream flows out of the cave.\n"
        "##### 2. GOBLIN BLIND\nTwo goblins keep watch.\n"
        "##### 3. KENNEL\nThree wolves are chained here.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "CRAGMAW HIDEOUT"
    )

    assert [item["key"] for item in scene.metadata["spatial"]["locations"]] == [
        "1-cave-mouth",
        "2-goblin-blind",
        "3-kennel",
    ]
    assert all(
        item["confidence"] == "explicit_heading"
        for item in scene.metadata["spatial"]["locations"]
    )


def test_spatial_connections_require_explicit_route_language() -> None:
    content = (
        "# Dungeon\n## Locations\n"
        "#### D1. Courtyard\nGuards from D3 join a fight here in round two.\n"
        "#### D2. North Room\nA secret door and stairs lead to D3.\n"
        "#### D3. Cellar\nA rat has visited rooms D1 to D3.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "Locations"
    )

    assert scene.metadata["spatial"]["connections"] == [
        {
            "from": "d2-north-room",
            "to": "d3-cellar",
            "bidirectional": True,
            "kind": "passage",
            "confidence": "explicit_text",
            "evidence": {"line": 5, "text": "lead to D3"},
        }
    ]


def test_spatial_connections_recognize_explicit_chinese_route_language() -> None:
    content = (
        "# 地城\n## 区域\n"
        "#### D4. 北部推拿房\n密门后的楼梯通向 D5。\n"
        "#### D5. 地下城入口\n房间一片黑暗。\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "区域"
    )

    connection = scene.metadata["spatial"]["connections"][0]
    assert connection["from"] == "d4"
    assert connection["to"] == "d5"
    assert connection["confidence"] == "explicit_text"
    assert connection["evidence"]["text"] == "通向 D5"


def test_statblock_headings_do_not_become_spatial_rooms() -> None:
    content = (
        "# Appendix B: Monsters\n## Statistics\n"
        "Armor Class 12\nHit Points 30\nSpeed 30 ft.\n"
        "#### SIZE\nA creature occupies a 5 by 5 feet space.\n"
        "#### SPEED\nWalking speed.\n"
        "## Monster Descriptions\n#### OGRE\nArmor Class 11\nHit Points 59\n"
    )

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    assert all(scene.metadata["scene_type"] == "reference" for scene in scenes)
    assert all(scene.metadata["spatial"]["locations"] == [] for scene in scenes)


def test_uncoded_location_heading_can_be_a_room_outside_reference_chapter() -> None:
    content = (
        "# The Spider's Web\n## Conyberry\n"
        "#### AGATHA'S LAIR\nThe banshee waits here.\n"
        "#### DEVELOPMENTS\nShe may answer one question.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "Conyberry"
    )

    assert [item["title"] for item in scene.metadata["spatial"]["locations"]] == [
        "AGATHA'S LAIR"
    ]


def test_read_aloud_fragments_do_not_split_scenes() -> None:
    content = (
        "# Tomb\n## Nine Shrines\nDescription.\n"
        "## I A strange grid is etched into the far wall of this stone cell. I\n"
        "Read-aloud continuation.\n## Final Chamber\nDescription.\n"
    )

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    assert [scene.title for scene in scenes] == ["Tomb", "Nine Shrines", "Final Chamber"]


def test_coded_scene_fallback_is_typed_as_room_even_with_ocr_digit() -> None:
    content = "# Lair\n## Ql. Central Hub\nThe corridor leads onward.\n"

    scene = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes[1]

    assert scene.title == "Ql. Central Hub"
    assert scene.metadata["spatial"]["locations"][0]["kind"] == "room"
    assert scene.metadata["spatial"]["locations"][0]["confidence"] == "explicit_heading"


def test_chapter_preamble_does_not_create_a_spatial_room() -> None:
    content = "# Tomb of the Nine Gods\nOverview.\n## Rotten Halls\nDescription.\n"

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    assert scenes[0].metadata["scene_type"] == "overview"
    assert scenes[0].metadata["spatial"]["locations"] == []
    assert scenes[1].metadata["spatial"]["locations"][0]["kind"] == "scene"
