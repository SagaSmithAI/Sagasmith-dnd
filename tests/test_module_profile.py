from __future__ import annotations

from sagasmith_core.modules import MarkdownModuleParser

from sagasmith_dnd.module_profile import DndModuleProfile


def test_module_profile_version_includes_inline_ocr_room_recovery() -> None:
    assert DndModuleProfile.version == "29"


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


def test_flowchart_connector_does_not_swallow_authored_h4_encounters() -> None:
    content = (
        "# Ch. 4: Dragon Season\n"
        "Chapter overview.\n"
        "### 1 l ENCOUNTER 3,\n"
        "A flow-chart extraction fragment.\n"
        "#### ENCOUNTER 1: ALLEY\n"
        "The chase begins.\n"
        "#### ENCOUNTER 2: MISTSHORE\n"
        "The trail reaches the docks.\n"
        "#### ENCOUNTER 3: STREET CHASE\n"
        "The pursuit continues.\n"
    )

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    assert "1 l ENCOUNTER 3," not in [scene.title for scene in scenes]
    assert [scene.title for scene in scenes[1:]] == [
        "ENCOUNTER 1: ALLEY",
        "ENCOUNTER 2: MISTSHORE",
        "ENCOUNTER 3: STREET CHASE",
    ]
    assert all(scene.metadata["scene_level"] == 4 for scene in scenes)


def test_encounter_chain_flowchart_is_overview_not_spatial_atlas_evidence() -> None:
    content = (
        "# Ch. 4: Dragon Season\n"
        "#### ENCOUNT ER CHAINS BY S EASON\n"
        "##### CELLAR COMPLEX\n"
        "A diagram arrow points onward.\n"
        "##### OLD TOWER\n"
        "Another diagram label.\n"
        "##### CELLAR COMPLEX\n"
        "The same label appears on another seasonal route.\n"
        "##### OLD TOWER\n"
        "The same destination appears again.\n"
        "#### ENCOUNTER 1: ALLEY\n"
        "The authored encounter begins here.\n"
    )

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
    diagram = next(scene for scene in scenes if "CHAINS BY S EASON" in scene.title)

    assert diagram.metadata["scene_type"] == "overview"
    assert diagram.metadata["spatial"]["locations"] == []
    assert diagram.metadata["spatial"]["connections"] == []


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
        "##### HIDEOUT\nA repeated PDF page header.\n"
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


def test_scene_atlas_recovers_numbered_room_lines_missing_markdown_markers() -> None:
    content = (
        "# Episode 3\n## Dragon Hatchery\n"
        "##### 1. C ave E n tr a n ce\nTwo guards stand inside.\n"
        "2. C o n c e a l e d Passa g e\nA passage is hidden in shadow.\n"
        "3. F u n g u s G a r d e n\nThe stairs lead to a fungus garden.\n"
        "##### 4. St ir g e L a ir\nTen stirges roost here.\n"
        "10A. B l a c k D r a g o n E ggs\nThree eggs stand below.\n"
        "10B. K o bo ld s i n H id in g\nFour kobolds wait here.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "Dragon Hatchery"
    )

    locations = scene.metadata["spatial"]["locations"]
    assert [item["title"] for item in locations] == [
        "1. C ave E n tr a n ce",
        "2. C o n c e a l e d Passa g e",
        "3. F u n g u s G a r d e n",
        "4. St ir g e L a ir",
        "10A. B l a c k D r a g o n E ggs",
        "10B. K o bo ld s i n H id in g",
    ]
    assert [item["confidence"] for item in locations] == [
        "explicit_heading",
        "explicit_text_heading",
        "explicit_text_heading",
        "explicit_heading",
        "explicit_text_heading",
        "explicit_text_heading",
    ]
    assert scene.metadata["spatial"]["connections"] == []


def test_scene_atlas_recovers_inline_ocr_room_one_and_split_title() -> None:
    content = (
        "# Episode 2\n## Ice Caves\n"
        "The ice trolls defend area 12. L E n t r a n c e f r o m H u t "
        "Inside the hut, steep icy stairs descend into a rectangular chamber.\n"
        "2. E n t r a n c e f r o m\n"
        "t h e V i l l a g e H a l l Hidden beneath the planks is another chute.\n"
        "##### 3. L a r d e r\nFrozen fish is stored here.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "Ice Caves"
    )

    assert scene.metadata["spatial"]["locations"] == [
        {
            "key": "1-e-n-t-r-a-n-c-e-f-r-o-m-h-u-t",
            "title": "1. E n t r a n c e f r o m H u t",
            "kind": "room",
            "line": 2,
            "dimensions_ft": None,
            "confidence": "explicit_ocr_text_heading",
        },
        {
            "key": "2-e-n-t-r-a-n-c-e-f-r-o-m-t-h-e-v-i-l-l-a-g-e-h-a-l-l",
            "title": "2. E n t r a n c e f r o m t h e V i l l a g e H a l l",
            "kind": "room",
            "line": 3,
            "dimensions_ft": None,
            "confidence": "explicit_text_heading",
        },
        {
            "key": "3-l-a-r-d-e-r",
            "title": "3. L a r d e r",
            "kind": "room",
            "line": 6,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        },
    ]


def test_scene_atlas_recovers_ocr_room_one_heading_without_period() -> None:
    content = (
        "# Episode 7\n## The Maze\n"
        "The maze has seven encounter nodes.\n"
        "##### L T h e Su n d i a l\n"
        "After walking into the maze, the characters arrive at an intersection.\n"
        "##### 2. C h u u l P o o l\n"
        "Four chuuls dwell in the pool.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "The Maze"
    )

    assert scene.metadata["spatial"]["locations"] == [
        {
            "key": "1-t-h-e-su-n-d-i-a-l",
            "title": "1. T h e Su n d i a l",
            "kind": "room",
            "line": 4,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        },
        {
            "key": "2-c-h-u-u-l-p-o-o-l",
            "title": "2. C h u u l P o o l",
            "kind": "room",
            "line": 6,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        },
    ]


def test_scene_atlas_orders_mixed_heading_evidence_by_scene_offset() -> None:
    content = (
        "# Episode 2\n"
        + ("Campaign preamble.\n" * 50)
        + "## Ice Caves\n"
        "##### 3. L a r d e r\nFrozen fish is stored here.\n"
        "7. H a l l o f G i a n t s\nFrozen giants line the walls.\n"
        "##### 12. Ic e T r o l l s\nThe trolls lair here.\n"
    )
    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "Ice Caves"
    )

    assert [item["key"] for item in scene.metadata["spatial"]["locations"]] == [
        "3-l-a-r-d-e-r",
        "7-h-a-l-l-o-f-g-i-a-n-t-s",
        "12-ic-e-t-r-o-l-l-s",
    ]


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


def test_proper_named_destination_uses_authored_arrival_prose_as_location_evidence() -> None:
    content = (
        "# A Friend in Need\n"
        "## FINDING FLOON\n"
        "The investigation begins.\n"
        "### WHERE TO START\n"
        "When the characters arrive at the Yawning Portal, begin the chapter.\n"
        "### THE SKEWERED DRAGON\n"
        "The Skewered Dragon faces an alley in the Dock Ward. "
        "When the characters ap\u0002proach it, read the following.\n"
        "### DEVELOPMENTS\n"
        "When the characters approach the end of the investigation, guards arrive.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "FINDING FLOON"
    )

    assert [item["title"] for item in scene.metadata["spatial"]["locations"]] == [
        "THE SKEWERED DRAGON"
    ]


def test_named_action_section_populates_scene_atlas_without_claiming_it_is_a_room() -> None:
    content = (
        "# A Friend in Need\n"
        "## FINDING FLOON\n"
        "The investigation begins.\n"
        "### TRACKING FLOON\n"
        "At this point, the characters know that Floon was kidnapped. "
        "A successful DC 15 Intelligence (Investigation) check or 5 gp in bribes "
        "allows the characters to trace the kidnappers to a sewer cover.\n"
        "### DEVELOPMENTS\n"
        "The City Watch arrives after the characters finish.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "FINDING FLOON"
    )

    assert scene.metadata["subsections"] == [
        {"title": "TRACKING FLOON", "line": 4, "type": "scene"},
        {"title": "DEVELOPMENTS", "line": 6, "type": "section"},
    ]
    assert scene.metadata["spatial"]["locations"] == [
        {
            "key": "tracking-floon",
            "title": "TRACKING FLOON",
            "kind": "scene",
            "line": 4,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        }
    ]


def test_ocr_split_chase_variants_remain_distinct_scene_atlas_locations() -> None:
    content = (
        "# Dragon Season\n"
        "## STREET CHASE\n"
        "Use the urban chase rules.\n"
        "### STREET CHA SE: S PRING\n"
        "A kenku is 60 feet away from the characters at the start of the chase.\n"
        "### NEXT E NCOUNTER\n"
        "Proceed with encounter 7, Old Tower.\n"
        "### STREET CHA SE: SUMMER\n"
        "A doppelganger flees through crowded streets.\n"
        "### STREET CHASE: WIN TER\n"
        "A spy leads the characters through snowy streets.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "STREET CHASE"
    )

    assert [
        (item["key"], item["title"], item["kind"])
        for item in scene.metadata["spatial"]["locations"]
    ] == [
        ("street-cha-se-s-pring", "STREET CHA SE: S PRING", "scene"),
        ("street-cha-se-summer", "STREET CHA SE: SUMMER", "scene"),
        ("street-chase-win-ter", "STREET CHASE: WIN TER", "scene"),
    ]


def test_visual_fragment_does_not_hide_interaction_from_scene_atlas() -> None:
    content = (
        "# Fireball\n"
        "## AFTER THE BLAST\n"
        "The City Watch arrives at the crime scene.\n"
        "### THE WATCH ARRIVES\n"
        "Barnibus questions the characters.\n"
        "## jump lo conclusions. They both prefer to have ironclad\n"
        "evidence before making any arrests. Characters who seem truthful and "
        "honest can press Barnibus for further information by making a DC 15 "
        "Charisma (Persuasion) check.\n"
        "### EYEWITNESSES\n"
        "Several witnesses are eager to talk.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "AFTER THE BLAST"
    )

    assert scene.metadata["subsections"] == [
        {"title": "THE WATCH ARRIVES", "line": 4, "type": "scene"},
        {"title": "EYEWITNESSES", "line": 8, "type": "section"},
    ]
    assert scene.metadata["spatial"]["locations"] == [
        {
            "key": "the-watch-arrives",
            "title": "THE WATCH ARRIVES",
            "kind": "scene",
            "line": 4,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        }
    ]


def test_repeated_interaction_headings_receive_stable_unique_location_keys() -> None:
    content = (
        "# Rooftop Chase\n"
        "## ROOFTOP CHASE\n"
        "The chase begins.\n"
        "### CHASE COMPLICATION\n"
        "The characters must make a DC 12 Dexterity check to cross the roof.\n"
        "### CHASE COMPLICATION\n"
        "The characters must make a DC 10 Strength check to clear the gap.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "ROOFTOP CHASE"
    )

    assert [item["key"] for item in scene.metadata["spatial"]["locations"]] == [
        "chase-complication",
        "chase-complication-2",
    ]


def test_named_house_subsection_is_a_physical_scene_location() -> None:
    content = (
        "# Fireball\n"
        "## NIM'S SECRET\n"
        "The investigation continues.\n"
        "### HOUSE OF INSPIRED HANDS\n"
        "If the characters visit the temple, a mechanical bird flies toward them.\n"
        "### INSIDE THE TEMPLE\n"
        "The temple is open during daylight hours.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "NIM'S SECRET"
    )

    assert [item["key"] for item in scene.metadata["spatial"]["locations"]] == [
        "house-of-inspired-hands",
        "inside-the-temple",
    ]
    assert all(
        item["kind"] == "room" for item in scene.metadata["spatial"]["locations"]
    )


def test_ocr_split_house_subsection_remains_a_physical_location() -> None:
    content = (
        "# Fireball\n"
        "## NIM'S SECRET\n"
        "The investigation continues.\n"
        "### H OUSE OF I NSPIRED HANDS\n"
        "If the characters visit the temple, a mechanical bird flies toward them.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "NIM'S SECRET"
    )

    assert scene.metadata["spatial"]["locations"][0] == {
        "key": "h-ouse-of-i-nspired-hands",
        "title": "H OUSE OF I NSPIRED HANDS",
        "kind": "room",
        "line": 4,
        "dimensions_ft": None,
        "confidence": "explicit_heading",
    }


def test_named_windmill_season_populates_scene_atlas_as_a_location() -> None:
    content = (
        "# Dragon Season\n"
        "## W8. BACK ROOM\n"
        "The roof has collapsed.\n"
        "### CONVERTED WINDMILL: SPRING\n"
        "The key leads the characters to an old windmill where two commoners "
        "are hiding in an upper-floor apartment.\n"
        "### NEXT ENCOUNTER\n"
        "Proceed to the cellar complex.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "W8. BACK ROOM"
    )

    assert scene.metadata["spatial"]["locations"] == [
        {
            "key": "w8-back-room",
            "title": "W8. BACK ROOM",
            "kind": "room",
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        },
        {
            "key": "converted-windmill-spring",
            "title": "CONVERTED WINDMILL: SPRING",
            "kind": "room",
            "line": 4,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        }
    ]


def test_npc_visit_and_invitation_populates_scene_atlas_as_an_interaction() -> None:
    content = (
        "# Trollskull Alley\n"
        "## FACTION MISSIONS\n"
        "The factions contact promising adventurers.\n"
        "### SAVRA BELABRANTA\n"
        "Savra visits the characters' residence and invites them to the Halls "
        "of Justice, where they can be sworn into the order.\n"
        "### LEVEL ADVANCEMENT\n"
        "The characters should advance after engaging in faction missions.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "FACTION MISSIONS"
    )

    assert scene.metadata["subsections"] == [
        {"title": "SAVRA BELABRANTA", "line": 4, "type": "scene"},
        {"title": "LEVEL ADVANCEMENT", "line": 6, "type": "section"},
    ]
    assert scene.metadata["spatial"]["locations"] == [
        {
            "key": "savra-belabranta",
            "title": "SAVRA BELABRANTA",
            "kind": "scene",
            "line": 4,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        }
    ]


def test_town_businesses_at_subsection_level_populate_scene_atlas() -> None:
    content = (
        "# Part 2: Phandalin\n"
        "## TOWN DESCRIPTION\n"
        "### STONEHILL INN\nThe local inn has six rooms for rent.\n"
        "### BARTHEN'S PROVISIONS\nBarthen accepts wagon deliveries here.\n"
        "### LIONSHIELD COSTER\nLinene trades from this merchant house.\n"
        "### PHANDALIN MINER'S EXCHANGE\nLocal miners have their finds weighed.\n"
        "### ALDERLEAF FARM\nThe farm lies at the edge of town.\n"
        "### EDERMATH ORCHARD\nFruit trees surround a tidy cottage.\n"
        "### SLEEPING GIANT TAP HOUSE\nRedbrands frequent this place.\n"
        "### QUEST: ORE TROUBLE\nHarbin offers a reward.\n"
        "#### SHRINE OF LUCK\nA small shrine stands nearby.\n"
        "## REDBRAND HIDEOUT\nThe cellar lies under Tresendar Manor.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "TOWN DESCRIPTION"
    )

    assert [item["title"] for item in scene.metadata["spatial"]["locations"]] == [
        "STONEHILL INN",
        "BARTHEN'S PROVISIONS",
        "LIONSHIELD COSTER",
        "PHANDALIN MINER'S EXCHANGE",
        "ALDERLEAF FARM",
        "EDERMATH ORCHARD",
        "SLEEPING GIANT TAP HOUSE",
        "SHRINE OF LUCK",
    ]
    assert "QUEST: ORE TROUBLE" not in [
        item["title"] for item in scene.metadata["spatial"]["locations"]
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


def test_coded_scene_keeps_root_room_when_it_has_spatial_subsections() -> None:
    content = (
        "# Dragon Season\n"
        "## V9. MAIN VAULT\n"
        "A dragon guards the main vault.\n"
        "### LEAVING THE VAULT\n"
        "Enemies wait outside.\n"
        "### FACTION REINFORCEMENTS\n"
        "Allies can arrive here.\n"
    )

    scene = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes[1]

    assert [item["title"] for item in scene.metadata["spatial"]["locations"]] == [
        "V9. MAIN VAULT",
        "LEAVING THE VAULT",
    ]
    assert scene.metadata["spatial"]["locations"][0] == {
        "key": "v9-main-vault",
        "title": "V9. MAIN VAULT",
        "kind": "room",
        "dimensions_ft": None,
        "confidence": "explicit_heading",
    }


def test_ocr_room_codes_split_scenes_without_treating_words_as_codes() -> None:
    content = (
        "# Chapter 5\n"
        "#### XlO. NOSKA'S QUARTERS\n"
        "A rust monster waits in a cage.\n"
        "#### Xll. AHMAERGO'S COLLECTION\n"
        "A stuffed minotaur stands here.\n"
        "#### Xl3. THORVIN'S WORKSHOP\n"
        "Thorvin is building a contraption.\n"
        "#### Xl7. PROMENADE\n"
        "Pillars carved with eyes follow the hall.\n"
        "#### Xl9. XANATHAR'S SANCTUM\n"
        "A fishbowl dominates the room.\n"
        "#### FOO. Ordinary Section\n"
        "This alphabetic abbreviation is not a numbered room.\n"
        "#### BOW1. (The fish keeper uses the pallet as a bed.)\n"
        "This OCR sentence fragment is not a numbered room.\n"
    )

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    room_scenes = [scene for scene in scenes if scene.title.startswith("Xl")]
    assert [scene.title for scene in room_scenes] == [
        "XlO. NOSKA'S QUARTERS",
        "Xll. AHMAERGO'S COLLECTION",
        "Xl3. THORVIN'S WORKSHOP",
        "Xl7. PROMENADE",
        "Xl9. XANATHAR'S SANCTUM",
    ]
    assert all(
        scene.metadata["spatial"]["locations"][0]["kind"] == "room"
        for scene in room_scenes
    )
    ordinary = next(scene for scene in scenes if scene.title == "FOO. Ordinary Section")
    assert ordinary.metadata["spatial"]["locations"][0]["kind"] != "room"
    fragment = next(scene for scene in scenes if scene.title.startswith("BOW1."))
    assert fragment.metadata["spatial"]["locations"][0]["kind"] != "room"


def test_spaced_numeric_room_code_uses_canonical_location_and_route_keys() -> None:
    content = (
        "# Castle in the Clouds\n"
        "## SKYREACH CASTLE\n"
        "The icy passages connect the castle.\n"
        "##### 2 4 . I C E T U N N E L\n"
        "This passage leads to area 25.\n"
        "##### 2 5 . M A I N V A U L T\n"
        "A white dragon guards the hoard.\n"
    )

    scene = next(
        item
        for item in MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes
        if item.title == "SKYREACH CASTLE"
    )

    assert [item["key"] for item in scene.metadata["spatial"]["locations"]] == [
        "24-i-c-e-t-u-n-n-e-l",
        "25-m-a-i-n-v-a-u-l-t",
    ]
    assert scene.metadata["spatial"]["connections"] == [
        {
            "from": "24-i-c-e-t-u-n-n-e-l",
            "to": "25-m-a-i-n-v-a-u-l-t",
            "bidirectional": True,
            "kind": "passage",
            "confidence": "explicit_text",
            "evidence": {"line": 4, "text": "leads to area 25"},
        }
    ]


def test_chapter_preamble_does_not_create_a_spatial_room() -> None:
    content = "# Tomb of the Nine Gods\nOverview.\n## Rotten Halls\nDescription.\n"

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    assert scenes[0].metadata["scene_type"] == "overview"
    assert scenes[0].metadata["spatial"]["locations"] == []
    assert scenes[1].metadata["spatial"]["locations"][0]["kind"] == "scene"


def test_chapter_preamble_preserves_explicit_tavern_encounter_location() -> None:
    content = (
        "# A Friend in Need\n"
        "Chapter introduction.\n"
        "### TAVERN BRAWL\n"
        "A fight breaks out in the Yawning Portal taproom.\n"
        "### jumped outside the shop by rough-looking men in black\n"
        "Read-aloud continuation.\n"
        "## FINDING FLOON\n"
        "The investigation begins.\n"
    )

    scenes = MarkdownModuleParser(profile=DndModuleProfile()).parse(content)[0].scenes

    preamble = scenes[0]
    assert preamble.metadata["scene_type"] == "overview"
    assert preamble.metadata["subsections"] == [
        {"title": "TAVERN BRAWL", "line": 3, "type": "room"},
        {
            "title": "jumped outside the shop by rough-looking men in black",
            "line": 5,
            "type": "section",
        },
    ]
    assert preamble.metadata["spatial"]["locations"] == [
        {
            "key": "tavern-brawl",
            "title": "TAVERN BRAWL",
            "kind": "room",
            "line": 3,
            "dimensions_ft": None,
            "confidence": "explicit_heading",
        }
    ]
