"""End-to-end domain exercises for authored and emergent campaign growth.

These tests model the state changes a table makes between playable episodes,
rather than checking the individual validators in isolation.
"""

from __future__ import annotations

import copy
import json

import pytest
from sagasmith_core.modules import MarkdownModuleParser

from sagasmith_dnd.content_packages import validate_dnd_content_package
from sagasmith_dnd.module_profile import DndModuleProfile
from sagasmith_dnd.playthrough import (
    new_playthrough_manifest,
    playthrough_source_bindings,
    validate_playthrough_manifest,
)

SOURCE_REF = {
    "purpose": "campaign_expansion",
    "asset_path": "table-canon.md",
    "asset_sha256": "a" * 64,
    "page_start": 1,
    "page_end": 1,
    "heading_path": ["Campaign expansion"],
    "content_sha256": "b" * 64,
}


def _lineage(
    module_id: str,
    classification: str,
    *,
    root: str,
    parent: str = "",
    generation: int = 0,
    scenes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "module_id": module_id,
        "classification": classification,
        "root_module_id": root,
        "parent_module_id": parent,
        "generation": generation,
        "scene_ids": list(scenes),
        "source_refs": [],
    }


def _playthrough(
    *,
    campaign_mode: str,
    lineage: list[dict[str, object]],
) -> dict[str, object]:
    return new_playthrough_manifest(
        run_id="runtime-test",
        campaign_line_id="runtime-line",
        module_ids=[str(item["module_id"]) for item in lineage],
        recommended_party_minimum=None,
        recommended_party_maximum=None,
        selected_party_size=None,
        source_refs=[],
        campaign_mode=campaign_mode,
        content_lineage=lineage,
    )


def _runtime_markdown(
    *,
    module_key: str,
    classification: str,
    root: str,
    parent: str = "",
    generation: int = 0,
    scene_title: str,
    scene_id: str,
    linked_from: str | None = None,
) -> str:
    thread_id = "thread:missing-caravan"
    clue_id = f"clue:{module_key}"
    manifest = {
        "schema_version": 2,
        "module_key": module_key,
        "classification": classification,
        "lineage": {
            "root_module_key": root,
            "parent_module_key": parent,
            "generation": generation,
        },
        "entities": [],
        "secrets": [],
        "clues": [
            {
                "id": clue_id,
                "label": "A caravan tally",
                "trigger": "The party searches the newly reached location.",
                "revelation": "One caravan never reached the eastern road.",
                "linked_thread_ids": [thread_id],
                "fallback_scene_ids": [scene_id],
            }
        ],
        "plot_nodes": [],
        "foreshadowing": [],
        "branches": [],
        "fronts": [
            {
                "id": "front:road-wardens",
                "name": "The road wardens close the passes",
                "goal": "Control travel before the caravan is found.",
                "stakes": "The party may be trapped beyond the border.",
                "grim_portents": ["A tollhouse stops answering messages."],
                "linked_thread_ids": [thread_id],
            }
        ],
        "story_threads": [
            {
                "id": thread_id,
                "title": "The missing caravan",
                "question": "Who diverted the caravan, and why?",
                "linked_front_ids": ["front:road-wardens"],
                "linked_clue_ids": [clue_id],
            }
        ],
        "character_arcs": [
            {
                "id": "arc:warden-trust",
                "actor_id": "pc:warden",
                "actor_kind": "pc",
                "opportunities": [
                    {
                        "id": f"opportunity:{module_key}",
                        "prompt": "Choose whether to trust a witness without proof.",
                        "scene_ids": [scene_id],
                        "thread_ids": [thread_id],
                    }
                ],
                "planned_beats": [],
                "possible_endings": [],
            }
        ],
        "scene_links": (
            []
            if linked_from is None
            else [
                {
                    "id": f"link:{module_key}",
                    "from_scene_id": linked_from,
                    "to_scene_id": scene_id,
                    "kind": "player_choice",
                    "trigger": "The party chooses the road beyond the current Atlas.",
                }
            ]
        ),
    }
    return (
        "<!-- sagasmith-runtime-manifest\n"
        + json.dumps(manifest, separators=(",", ":"))
        + f"\n-->\n# {module_key}\n\n## {scene_title}\n\nA playable scene.\n"
    )


def _parse_runtime(content: str) -> dict[str, object]:
    metadata = MarkdownModuleParser(profile=DndModuleProfile()).document_metadata(content)
    assert metadata["runtime_manifest_errors"] == []
    return metadata["runtime_manifest"]


def test_emergent_seed_grows_through_two_playable_episodes() -> None:
    seed = _parse_runtime(
        _runtime_markdown(
            module_key="ashen-road-seed",
            classification="emergent_seed",
            root="ashen-road-seed",
            scene_title="Crossroads",
            scene_id="scene:crossroads",
        )
    )
    episode_one = _parse_runtime(
        _runtime_markdown(
            module_key="ashen-road-tollhouse",
            classification="emergent_episode",
            root="ashen-road-seed",
            parent="ashen-road-seed",
            generation=1,
            scene_title="Eastern Tollhouse",
            scene_id="scene:tollhouse",
            linked_from="scene:crossroads",
        )
    )
    episode_two = _parse_runtime(
        _runtime_markdown(
            module_key="ashen-road-ferry",
            classification="emergent_episode",
            root="ashen-road-seed",
            parent="ashen-road-tollhouse",
            generation=2,
            scene_title="Silent Ferry",
            scene_id="scene:ferry",
            linked_from="scene:tollhouse",
        )
    )

    manifest = _playthrough(
        campaign_mode="emergent",
        lineage=[
            _lineage(
                "ashen-road-seed",
                "emergent_seed",
                root="ashen-road-seed",
                scenes=("scene:crossroads",),
            )
        ],
    )
    for shard, runtime in (
        ("ashen-road-tollhouse", episode_one),
        ("ashen-road-ferry", episode_two),
    ):
        manifest["module_ids"].append(shard)
        runtime_lineage = runtime["lineage"]
        manifest["content_lineage"].append(
            _lineage(
                shard,
                "emergent_episode",
                root=str(runtime_lineage["root_module_key"]),
                parent=str(runtime_lineage["parent_module_key"]),
                generation=int(runtime_lineage["generation"]),
                scenes=(str(runtime["scene_links"][0]["to_scene_id"]),),
            )
        )
        manifest = validate_playthrough_manifest(manifest)

    manifest["front_progress"] = [
        {
            "id": "front:road-wardens",
            "status": "advanced",
            "stage": 1,
            "source_ref": {**SOURCE_REF, "module_id": "ashen-road-tollhouse"},
            "evidence_refs": [{"kind": "event", "ref_id": "event:tollhouse-closed"}],
        }
    ]
    manifest["thread_progress"] = [
        {
            "id": "thread:missing-caravan",
            "status": "advanced",
            "source_ref": None,
            "evidence_refs": [{"kind": "scene", "ref_id": "scene:ferry"}],
        }
    ]
    manifest["arc_progress"] = [
        {
            "id": "arc:warden-trust",
            "actor_id": "pc:warden",
            "actor_kind": "pc",
            "status": "available",
            "completed_opportunity_ids": [],
            "source_ref": None,
            "evidence_refs": [
                {"kind": "conversation", "ref_id": "conversation:ferryman"}
            ],
        }
    ]
    validated = validate_playthrough_manifest(manifest)

    assert seed["classification"] == "emergent_seed"
    assert [item["generation"] for item in validated["content_lineage"]] == [0, 1, 2]
    assert validated["content_lineage"][2]["parent_module_id"] == "ashen-road-tollhouse"
    assert validated["arc_progress"][0]["completed_opportunity_ids"] == []
    assert [path for path, _ in playthrough_source_bindings(validated)] == [
        "front_progress[0].source_ref"
    ]


def test_authored_module_can_detour_off_atlas_without_rewriting_publisher_canon() -> None:
    authored = _parse_runtime(
        _runtime_markdown(
            module_key="published-keep",
            classification="authored_module",
            root="published-keep",
            scene_title="Keep Gate",
            scene_id="scene:keep-gate",
        )
    )
    detour = _parse_runtime(
        _runtime_markdown(
            module_key="published-keep-windmill",
            classification="emergent_episode",
            root="published-keep",
            parent="published-keep",
            generation=1,
            scene_title="Abandoned Windmill",
            scene_id="scene:windmill",
            linked_from="scene:keep-gate",
        )
    )
    root_lineage = _lineage(
        "published-keep",
        "authored_module",
        root="published-keep",
        scenes=("scene:keep-gate",),
    )
    original_root = copy.deepcopy(root_lineage)
    manifest = _playthrough(campaign_mode="authored_module", lineage=[root_lineage])

    manifest["campaign_mode"] = "authored_with_extensions"
    manifest["module_ids"].append("published-keep-windmill")
    manifest["content_lineage"].append(
        _lineage(
            "published-keep-windmill",
            "emergent_episode",
            root=str(detour["lineage"]["root_module_key"]),
            parent=str(detour["lineage"]["parent_module_key"]),
            generation=int(detour["lineage"]["generation"]),
            scenes=("scene:windmill",),
        )
    )
    validated = validate_playthrough_manifest(manifest)

    assert authored["classification"] == "authored_module"
    assert validated["campaign_mode"] == "authored_with_extensions"
    assert validated["content_lineage"][0] == original_root
    assert validated["content_lineage"][1]["classification"] == "emergent_episode"


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda value: value["content_lineage"][2].update(
                {"parent_module_id": "missing-parent"}
            ),
            "parent_module_id is not in module_ids",
        ),
        (
            lambda value: value["content_lineage"][2].update({"generation": 1}),
            "generation must equal its parent generation plus one",
        ),
        (
            lambda value: value["content_lineage"][2]["scene_ids"].append(
                "scene:tollhouse"
            ),
            "scene_ids must be unique across shards",
        ),
    ],
)
def test_scene_atlas_and_lineage_reject_broken_episode_chains(mutate, error: str) -> None:
    manifest = _playthrough(
        campaign_mode="emergent",
        lineage=[
            _lineage(
                "seed", "emergent_seed", root="seed", scenes=("scene:crossroads",)
            ),
            _lineage(
                "episode-1",
                "emergent_episode",
                root="seed",
                parent="seed",
                generation=1,
                scenes=("scene:tollhouse",),
            ),
            _lineage(
                "episode-2",
                "emergent_episode",
                root="seed",
                parent="episode-1",
                generation=2,
                scenes=("scene:ferry",),
            ),
        ],
    )
    mutate(manifest)

    with pytest.raises(ValueError, match=error):
        validate_playthrough_manifest(manifest)


def test_runtime_design_rejects_broken_links_and_predetermined_pc_arc() -> None:
    content = _runtime_markdown(
        module_key="broken-episode",
        classification="emergent_episode",
        root="seed",
        parent="seed",
        generation=1,
        scene_title="Broken Scene",
        scene_id="scene:broken",
        linked_from="scene:origin",
    )
    metadata = MarkdownModuleParser(profile=DndModuleProfile()).document_metadata(content)
    runtime = metadata["runtime_manifest"]
    runtime["story_threads"][0]["linked_front_ids"] = ["front:not-declared"]
    runtime["character_arcs"][0]["planned_beats"] = ["The warden must betray the party."]
    runtime["scene_links"] = []
    broken = (
        "<!-- sagasmith-runtime-manifest\n"
        + json.dumps(runtime, separators=(",", ":"))
        + "\n-->\n# Broken episode\n\n## Broken Scene\n"
    )

    errors = MarkdownModuleParser(profile=DndModuleProfile()).document_metadata(broken)[
        "runtime_manifest_errors"
    ]

    assert any("references unknown fronts id" in error for error in errors)
    assert any("PC may only define opportunities" in error for error in errors)
    assert "emergent_episode runtime manifest requires at least one scene_link" in errors


def test_authored_runtime_manifest_must_describe_an_immutable_generation_zero_root() -> None:
    content = _runtime_markdown(
        module_key="published-keep",
        classification="authored_module",
        root="published-keep",
        scene_title="Keep Gate",
        scene_id="scene:keep-gate",
    )
    metadata = MarkdownModuleParser(profile=DndModuleProfile()).document_metadata(content)
    runtime = metadata["runtime_manifest"]
    runtime["lineage"] = {
        "root_module_key": "some-other-root",
        "parent_module_key": "episode-before-publication",
        "generation": 4,
    }
    broken = (
        "<!-- sagasmith-runtime-manifest\n"
        + json.dumps(runtime, separators=(",", ":"))
        + "\n-->\n# Published keep\n\n## Keep Gate\n"
    )

    errors = MarkdownModuleParser(profile=DndModuleProfile()).document_metadata(broken)[
        "runtime_manifest_errors"
    ]

    assert any(
        "authored_module lineage must root at module_key" in error for error in errors
    )


def test_emergent_content_shard_cannot_activate_without_a_scene_atlas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = {
        "kind": "module",
        "system_id": "dnd5e",
        "metadata": {},
        "content": {
            "classification": "emergent_episode",
            "lineage": {
                "root_module_key": "seed",
                "parent_module_key": "seed",
                "generation": 1,
            },
            "scene_atlas": [],
        },
        "actors": [],
        "assets": [],
    }
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.validate_core_content_package",
        lambda value: copy.deepcopy(value),
    )

    with pytest.raises(ValueError, match="at least one Scene Atlas scene"):
        validate_dnd_content_package(package)


def test_episode_lineage_cannot_cross_between_two_campaign_roots() -> None:
    manifest = _playthrough(
        campaign_mode="emergent",
        lineage=[
            _lineage("seed-a", "emergent_seed", root="seed-a"),
            _lineage("seed-b", "emergent_seed", root="seed-b"),
        ],
    )
    manifest["module_ids"].append("episode-b-1")
    manifest["content_lineage"].append(
        _lineage(
            "episode-b-1",
            "emergent_episode",
            root="seed-a",
            parent="seed-b",
            generation=1,
            scenes=("scene:cross-root",),
        )
    )

    with pytest.raises(ValueError, match="must match its parent lineage"):
        validate_playthrough_manifest(manifest)


def test_episode_generation_must_increment_its_parent_by_exactly_one() -> None:
    manifest = _playthrough(
        campaign_mode="emergent",
        lineage=[_lineage("seed", "emergent_seed", root="seed")],
    )
    manifest["module_ids"].append("episode-1")
    manifest["content_lineage"].append(
        _lineage(
            "episode-1",
            "emergent_episode",
            root="seed",
            parent="seed",
            generation=7,
            scenes=("scene:distant-future",),
        )
    )

    with pytest.raises(ValueError, match="generation must equal its parent generation plus one"):
        validate_playthrough_manifest(manifest)


@pytest.mark.parametrize(
    ("collection", "item"),
    [
        (
            "front_progress",
            {"id": "front:road", "status": "advanced", "stage": 1},
        ),
        ("thread_progress", {"id": "thread:caravan", "status": "resolved"}),
        (
            "arc_progress",
            {
                "id": "arc:trust",
                "actor_id": "pc:warden",
                "actor_kind": "pc",
                "status": "resolved",
                "completed_opportunity_ids": ["opportunity:trust"],
            },
        ),
    ],
)
def test_material_progress_requires_runtime_evidence(
    collection: str, item: dict[str, object]
) -> None:
    manifest = _playthrough(
        campaign_mode="emergent",
        lineage=[_lineage("seed", "emergent_seed", root="seed")],
    )
    manifest[collection] = [{**item, "source_ref": None, "evidence_refs": []}]

    with pytest.raises(ValueError, match="requires evidence_refs"):
        validate_playthrough_manifest(manifest)
