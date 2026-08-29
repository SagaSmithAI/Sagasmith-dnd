from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from mcp.types import ImageContent, TextContent
from PIL import Image
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.combat_render import _disposition_color, render_combat_png
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server
from sagasmith_dnd_mcp.storage import SagaSmithStorage


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )


def _portrait_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (20, 30), "#315a72").save(output, format="PNG")
    return output.getvalue()


def _map_bytes(*, size: tuple[int, int] = (80, 80)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "#bc693f").save(output, format="PNG")
    return output.getvalue()


def _party_public_asset(content: bytes) -> dict:
    with Image.open(BytesIO(content)) as image:
        width, height = image.size
    return {
        "asset_key": "reviewed-map",
        "checksum": hashlib.sha256(content).hexdigest(),
        "media_type": "image/png",
        "width": width,
        "height": height,
        "alt_text": "A reviewed public gatehouse map.",
        "license": "private party display",
        "attribution": "User-supplied artwork.",
        "grid_alignment": {
            "mode": "contain",
            "x": 0,
            "y": 0,
            "width_cells": 4,
            "height_cells": 2,
        },
        "review": {
            "status": "approved",
            "audience": "party_public",
            "reviewer": "dm:keeper",
            "reviewed_at": "2026-08-28T00:00:00Z",
            "note": "No hidden geometry or DM annotations.",
        },
    }


def _public_asset_encounter(content: bytes) -> dict:
    return {
        "id": "encounter-public",
        "name": "Public map",
        "positioning_mode": "grid",
        "round": 1,
        "turn_index": 0,
        "battle_map": {
            "map_revision": 1,
            "grid": {"kind": "square", "cell_ft": 5},
            "bounds": {"width_cells": 4, "height_cells": 2},
            "difficult_cells": ["1,0"],
            "party_public_map_asset": _party_public_asset(content),
        },
        "combatants": [
            {
                "actor_id": "hero-internal",
                "name": "Hero",
                "initiative": 18,
                "position": {"x": 2, "y": 1},
            }
        ],
    }


def test_party_public_map_artwork_is_letterboxed_beneath_authoritative_overlays() -> None:
    artwork = _map_bytes(size=(80, 80))
    encounter = _public_asset_encounter(artwork)

    metadata, content = render_combat_png(
        encounter,
        audience_projection="party_public",
        party_public_map_asset=artwork,
    )

    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert metadata["decorative_map_asset"] == {
        "used": True,
        "fallback": None,
        "letterboxed": True,
        "alt_text": "A reviewed public gatehouse map.",
        "license": "private party display",
        "attribution": "User-supplied artwork.",
        "grid_alignment": {
            "mode": "contain",
            "x": 0,
            "y": 0,
            "width_cells": 4,
            "height_cells": 2,
        },
    }
    assert "reviewed-map" not in json.dumps(metadata)
    assert "dm:keeper" not in json.dumps(metadata)
    assert "Map artwork: A reviewed public gatehouse map." in metadata["alt_text"]
    with Image.open(BytesIO(content)) as rendered:
        # The square art is contained inside a 4x2-cell target: the side band is
        # letterbox, visible art remains in cell 2, and authoritative terrain
        # covers the artwork in difficult cell 1.
        assert rendered.getpixel((90, 192)) == (32, 36, 31)
        assert rendered.getpixel((230, 192)) == (188, 105, 63)
        assert rendered.getpixel((166, 192)) == (102, 83, 51)


def test_invalid_or_non_public_map_artwork_uses_deterministic_fallback() -> None:
    artwork = _map_bytes()
    encounter = _public_asset_encounter(artwork)
    private_only = deepcopy(encounter)
    private_only["battle_map"].pop("party_public_map_asset")
    private_only["battle_map"]["map_asset_key"] = "private-source-map"

    bad_checksum = deepcopy(encounter)
    bad_checksum["battle_map"]["party_public_map_asset"]["checksum"] = "0" * 64
    unapproved = deepcopy(encounter)
    unapproved["battle_map"]["party_public_map_asset"]["review"]["status"] = "pending"
    cases = (
        (encounter, "caller"),
        (private_only, "party_public"),
        (bad_checksum, "party_public"),
        (unapproved, "party_public"),
    )
    outputs = []
    for value, projection in cases:
        metadata, rendered = render_combat_png(
            value,
            audience_projection=projection,
            party_public_map_asset=artwork,
        )
        assert metadata["decorative_map_asset"] == {
            "used": False,
            "fallback": "deterministic_texture",
        }
        outputs.append(rendered)

    repeated = render_combat_png(
        unapproved,
        audience_projection="party_public",
        party_public_map_asset=artwork,
    )[1]
    assert outputs[-1] == repeated


def test_render_combat_png_is_deterministic_and_uses_portrait() -> None:
    encounter = {
        "id": "encounter-1",
        "name": "The Broken Gate",
        "positioning_mode": "grid",
        "round": 2,
        "turn_index": 0,
        "battle_map": {
            "map_revision": 3,
            "grid": {"kind": "square", "cell_ft": 5},
            "bounds": {"width_cells": 5, "height_cells": 4},
            "blocked_cells": ["2,1"],
            "difficult_cells": ["3,2"],
        },
        "combatants": [
            {
                "actor_id": "actor-internal-hero",
                "name": "Hero",
                "initiative": 18,
                "position": {"x": 1, "y": 1},
                "disposition": "friendly",
                "hp": {"current": 3, "max": 99},
                "conditions": ["secret-mark"],
            },
            {
                "actor_id": "actor-internal-foe",
                "name": "Foe",
                "initiative": 12,
                "position": {"x": 4, "y": 2},
                "disposition": "hostile",
            },
        ],
        "source": {"location_key": "secret-vault"},
    }
    first_metadata, first = render_combat_png(
        encounter,
        portraits={"actor-internal-hero": _portrait_bytes()},
        audience_projection="party_public",
    )
    second_metadata, second = render_combat_png(
        encounter,
        portraits={"actor-internal-hero": _portrait_bytes()},
        audience_projection="party_public",
    )

    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    assert first == second
    assert first_metadata == second_metadata
    assert first_metadata["image_checksum"] == hashlib.sha256(first).hexdigest()
    assert first_metadata["map_revision"] == 3
    assert first_metadata["current_actor_id"] == "actor-internal-hero"
    assert "Hero" in first_metadata["alt_text"]
    share_payload = json.dumps(
        {
            "alt_text": first_metadata["alt_text"],
            "share_card": first_metadata["share_card"],
            "suggested_caption": first_metadata["suggested_caption"],
        },
        ensure_ascii=False,
    )
    for private_value in (
        "actor-internal-hero",
        "actor-internal-foe",
        "secret-mark",
        "secret-vault",
    ):
        assert private_value not in share_payload
    assert set(first_metadata["share_card"]["roster"][0]) == {
        "name",
        "initiative",
        "position",
    }


def test_missing_disposition_uses_unknown_instead_of_friendly_color() -> None:
    assert _disposition_color(None) == "#69716c"
    assert _disposition_color("undisclosed") == "#69716c"
    assert _disposition_color("friendly") == "#637b64"


def test_managed_actor_image_is_checksum_bound(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.prepare()
    storage = SagaSmithStorage(config)
    content = _portrait_bytes()
    checksum = hashlib.sha256(content).hexdigest()

    stored = storage.store_actor_image(
        {"checksum": checksum, "media_type": "image/png"},
        content,
    )

    assert Path(stored["path"]).parent == config.actor_images_dir
    assert storage.read_actor_image(checksum) == content
    assert storage.read_actor_image("../portrait.png") is None


def test_combat_query_render_returns_image_and_party_projection_hides_actor(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Render", "edition": "2014", "idempotency_key": "campaign"},
        )
        characters = []
        for key, name in (("hero", "Visible Hero"), ("foe", "Hidden Foe")):
            created = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": name,
                        "character_type": "monster" if key == "foe" else "pc",
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": key,
                },
            )
            characters.append(created)
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": "play",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "campaign_id": campaign["id"],
                "participant_ids": [item["id"] for item in characters],
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 6, "height_cells": 4},
                "participant_config": [
                    {
                        "actor_id": characters[0]["id"],
                        "initiative": 20,
                        "position": {"x": 1, "y": 1},
                    },
                    {
                        "actor_id": characters[1]["id"],
                        "initiative": 10,
                        "position": {"x": 4, "y": 1},
                        "hidden": True,
                    },
                ],
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "start",
            },
        )
        patched = await _call(
            server,
            "combat_map_patch",
            {
                "campaign_id": campaign["id"],
                "patches": [{"key": "secret-door", "value": True}],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "patch-map",
            },
        )

        rendered = await server.call_tool(
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "render",
                "payload": {"audience_projection": "party_public"},
            },
        )

        assert isinstance(rendered.content[0], TextContent)
        assert isinstance(rendered.content[1], ImageContent)
        metadata = json.loads(rendered.content[0].text)
        content = base64.b64decode(rendered.content[1].data)
        assert rendered.structured_content == metadata
        assert rendered.content[1].mime_type == "image/png"
        assert content.startswith(b"\x89PNG\r\n\x1a\n")
        assert metadata["audience_projection"] == "party_public"
        assert metadata["map_revision"] == 2
        assert "Visible Hero" in metadata["alt_text"]
        assert "Hidden Foe" not in metadata["alt_text"]
        assert metadata["campaign_revision"] == patched["campaign_revision"]
        share_payload = json.dumps(
            {
                "alt_text": metadata["alt_text"],
                "share_card": metadata["share_card"],
                "suggested_caption": metadata["suggested_caption"],
            },
            ensure_ascii=False,
        )
        assert characters[0]["id"] not in share_payload
        assert characters[1]["id"] not in share_payload
        assert "Hidden Foe" not in share_payload
        assert "secret-door" not in share_payload
        assert metadata["share_card"]["map_label"].endswith("rev 2")

    asyncio.run(exercise())
