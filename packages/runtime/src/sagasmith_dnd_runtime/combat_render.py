"""Deterministic, non-authoritative combat snapshot rendering."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

_PAPER = "#f5efdf"
_INK = "#171916"
_MUTED = "#777367"
_GRID_BG = "#20241f"
_GRID_TEXTURE = "#2a2f29"
_GRID_LINE = "#666b60"
_FRIENDLY = "#637b64"
_HOSTILE = "#b64732"
_NEUTRAL = "#b08c4e"
_UNKNOWN = "#69716c"
_ACCENT = "#e2522d"
_COORDINATE_GUTTER = 28


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    filenames = (
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    )
    for filename in filenames:
        if Path(filename).is_file():
            try:
                return ImageFont.truetype(filename, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, value: str, font: Any, width: int) -> str:
    text = " ".join(str(value or "").split())
    if draw.textlength(text, font=font) <= width:
        return text
    suffix = "..."
    while text and draw.textlength(text + suffix, font=font) > width:
        text = text[:-1]
    return text + suffix


def _disposition_color(value: Any) -> str:
    disposition = str(value or "").casefold()
    if disposition == "friendly":
        return _FRIENDLY
    if disposition == "hostile":
        return _HOSTILE
    if disposition == "neutral":
        return _NEUTRAL
    return _UNKNOWN


def _condition_labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels = []
    for item in value:
        label = (
            item.get("name") or item.get("id") or item.get("condition_id")
            if isinstance(item, Mapping)
            else item
        )
        normalized = " ".join(str(label or "").split())
        if normalized:
            labels.append(normalized)
    return labels


def _portrait_image(content: bytes | None, size: int) -> Image.Image | None:
    if not content:
        return None
    try:
        with Image.open(BytesIO(content)) as source:
            return ImageOps.fit(
                source.convert("RGB"),
                (size, size),
                method=Image.Resampling.LANCZOS,
            )
    except (OSError, ValueError):
        return None


def _party_public_map_image(
    battle_map: Mapping[str, Any],
    content: bytes | None,
    *,
    audience_projection: str,
) -> tuple[Image.Image, dict[str, Any]] | None:
    """Open one checksum-bound reviewed image; invalid decoration is always optional."""

    if audience_projection != "party_public" or not content:
        return None
    asset = battle_map.get("party_public_map_asset")
    if not isinstance(asset, Mapping):
        return None
    try:
        review = dict(asset.get("review") or {})
        if review.get("status") != "approved" or review.get("audience") != "party_public":
            return None
        if any(
            not str(asset.get(field) or "").strip()
            for field in ("alt_text", "license", "attribution")
        ):
            return None
        checksum = str(asset.get("checksum") or "").casefold()
        if hashlib.sha256(content).hexdigest() != checksum:
            return None
        expected_media_type = str(asset.get("media_type") or "").casefold()
        expected_format = {
            "image/jpeg": "JPEG",
            "image/png": "PNG",
            "image/webp": "WEBP",
        }.get(expected_media_type)
        if expected_format is None:
            return None
        with Image.open(BytesIO(content)) as source:
            if source.format != expected_format or source.size != (
                int(asset.get("width", 0) or 0),
                int(asset.get("height", 0) or 0),
            ):
                return None
            if source.width * source.height > 32 * 1024 * 1024:
                return None
            source.load()
            artwork = source.convert("RGB")
        alignment = dict(asset.get("grid_alignment") or {})
        if alignment.get("mode") != "contain":
            return None
        bounds = dict(battle_map.get("bounds") or {})
        width_cells = int(bounds.get("width_cells", 0) or 0)
        height_cells = int(bounds.get("height_cells", 0) or 0)
        x = int(alignment.get("x", -1))
        y = int(alignment.get("y", -1))
        aligned_width = int(alignment.get("width_cells", 0))
        aligned_height = int(alignment.get("height_cells", 0))
        if (
            x < 0
            or y < 0
            or aligned_width < 1
            or aligned_height < 1
            or x + aligned_width > width_cells
            or y + aligned_height > height_cells
        ):
            return None
        return artwork, alignment
    except (Image.DecompressionBombError, OSError, TypeError, ValueError):
        return None


def _draw_round_portrait(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    diameter: int,
    actor: Mapping[str, Any],
    portrait: bytes | None,
    current: bool,
) -> None:
    left = center[0] - diameter // 2
    top = center[1] - diameter // 2
    color = _disposition_color(actor.get("disposition"))
    picture = _portrait_image(portrait, diameter)
    if picture is not None:
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
        canvas.paste(picture, (left, top), mask)
    else:
        draw.ellipse((left, top, left + diameter, top + diameter), fill=color)
        label = str(actor.get("name") or actor.get("actor_id") or "?")[:2]
        font = _font(max(10, diameter // 3), bold=True)
        box = draw.textbbox((0, 0), label, font=font)
        draw.text(
            (
                center[0] - (box[2] - box[0]) / 2,
                center[1] - (box[3] - box[1]) / 2,
            ),
            label,
            fill=_PAPER,
            font=font,
        )
    border = _ACCENT if current else color
    draw.ellipse(
        (left - 3, top - 3, left + diameter + 3, top + diameter + 3),
        outline=border,
        width=5 if current else 3,
    )


def _current_actor_id(combatants: list[dict[str, Any]], turn_index: Any) -> str | None:
    if isinstance(turn_index, int) and 0 <= turn_index < len(combatants):
        return str(combatants[turn_index].get("actor_id") or "") or None
    return None


def _bounded_text_value(value: str, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _coordinate_step(cell_count: int) -> int:
    if cell_count <= 32:
        return 1
    if cell_count <= 80:
        return 5
    return 10


def _share_metadata(
    encounter: Mapping[str, Any],
    *,
    combatants: list[dict[str, Any]],
    current_actor_id: str | None,
    grid_mode: bool,
    width_cells: int,
    height_cells: int,
    map_revision: int | None,
) -> dict[str, Any]:
    """Build one compact, audience-safe card for chat and accessible clients."""

    title = _bounded_text_value(str(encounter.get("name") or "Combat"), 160)
    round_number = int(encounter.get("round", 1) or 1)
    current_actor = next(
        (
            actor
            for actor in combatants
            if str(actor.get("actor_id") or "") == current_actor_id
        ),
        None,
    )
    current_name = (
        _bounded_text_value(str(current_actor.get("name") or "Visible combatant"), 120)
        if current_actor is not None
        else None
    )
    roster = [
        {
            "name": _bounded_text_value(
                str(actor.get("name") or "Visible combatant"),
                120,
            ),
            "initiative": int(actor.get("initiative", 0) or 0),
            "position": (
                {
                    "x": int(actor["position"]["x"]),
                    "y": int(actor["position"]["y"]),
                }
                if grid_mode
                and isinstance(actor.get("position"), Mapping)
                and isinstance(actor["position"].get("x"), int)
                and isinstance(actor["position"].get("y"), int)
                else None
            ),
        }
        for actor in sorted(
            combatants,
            key=lambda item: -int(item.get("initiative", 0) or 0),
        )
    ]
    map_label = (
        f"{width_cells}x{height_cells} / 5 ft / rev {map_revision or 1}"
        if grid_mode
        else "Agent spatial mode / no coordinates"
    )
    lines = [f"⚔️ {title} · Round {round_number}"]
    if current_name:
        lines.append(f"▶️ Turn: {current_name}")
    lines.append(f"🗺️ {map_label}")
    if roster:
        roster_text = " · ".join(
            f"{item['name']} {item['initiative']}"
            + (
                f" @ {item['position']['x']},{item['position']['y']}"
                if item["position"] is not None
                else ""
            )
            for item in roster[:12]
        )
        if len(roster) > 12:
            roster_text += f" · +{len(roster) - 12} more"
        lines.append(f"👥 {roster_text}")
    return {
        "title": title,
        "round": round_number,
        "current_actor_name": current_name,
        "map_label": map_label,
        "visible_combatant_count": len(roster),
        "roster": roster,
        "suggested_caption": _bounded_text_value("\n".join(lines), 1800),
    }


def _draw_grid(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    battle_map: Mapping[str, Any],
    combatants: list[dict[str, Any]],
    portraits: Mapping[str, bytes],
    current_actor_id: str | None,
    left: int,
    top: int,
    width_cells: int,
    height_cells: int,
    cell: int,
    map_artwork: tuple[Image.Image, dict[str, Any]] | None,
) -> None:
    width = width_cells * cell
    height = height_cells * cell
    draw.rectangle((left, top, left + width, top + height), fill=_GRID_BG)
    if map_artwork is None:
        texture_step = max(18, min(42, cell))
        for offset in range(0, height, texture_step):
            stagger = (offset // texture_step % 2) * (texture_step // 2)
            draw.line(
                (
                    left,
                    top + offset,
                    left + width,
                    top + offset,
                ),
                fill=_GRID_TEXTURE,
                width=1,
            )
            for cross in range(stagger, width + texture_step, texture_step):
                draw.line(
                    (
                        left + cross,
                        top + offset,
                        left + cross - texture_step // 3,
                        top + min(height, offset + texture_step // 3),
                    ),
                    fill=_GRID_TEXTURE,
                    width=1,
                )
    else:
        artwork, alignment = map_artwork
        target_left = left + int(alignment["x"]) * cell
        target_top = top + int(alignment["y"]) * cell
        target_width = int(alignment["width_cells"]) * cell
        target_height = int(alignment["height_cells"]) * cell
        fitted = ImageOps.contain(
            artwork,
            (target_width, target_height),
            method=Image.Resampling.LANCZOS,
        )
        canvas.paste(
            fitted,
            (
                target_left + (target_width - fitted.width) // 2,
                target_top + (target_height - fitted.height) // 2,
            ),
        )
    coordinate_font = _font(max(10, min(15, cell // 4)), bold=True)
    x_step = _coordinate_step(width_cells)
    y_step = _coordinate_step(height_cells)
    for x in range(0, width_cells, x_step):
        draw.text(
            (left + x * cell + cell // 2, top - 9),
            str(x),
            fill=_MUTED,
            font=coordinate_font,
            anchor="ms",
        )
    for y in range(0, height_cells, y_step):
        draw.text(
            (left - 9, top + y * cell + cell // 2),
            str(y),
            fill=_MUTED,
            font=coordinate_font,
            anchor="rm",
        )
    difficult = set(battle_map.get("difficult_cells") or [])
    blocked = set(battle_map.get("blocked_cells") or [])
    for y in range(height_cells):
        for x in range(width_cells):
            key = f"{x},{y}"
            cell_left = left + x * cell
            cell_top = top + y * cell
            if key in difficult:
                draw.rectangle(
                    (cell_left, cell_top, cell_left + cell, cell_top + cell),
                    fill="#665333",
                )
            if key in blocked:
                draw.rectangle(
                    (cell_left, cell_top, cell_left + cell, cell_top + cell),
                    fill="#0c0d0c",
                )
                draw.line(
                    (cell_left + 2, cell_top + 2, cell_left + cell - 2, cell_top + cell - 2),
                    fill=_ACCENT,
                    width=2,
                )
    for x in range(width_cells + 1):
        pixel = left + x * cell
        draw.line((pixel, top, pixel, top + height), fill=_GRID_LINE, width=1)
    for y in range(height_cells + 1):
        pixel = top + y * cell
        draw.line((left, pixel, left + width, pixel), fill=_GRID_LINE, width=1)
    for actor in combatants:
        position = actor.get("position")
        if not isinstance(position, Mapping):
            continue
        x, y = position.get("x"), position.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            continue
        actor_id = str(actor.get("actor_id") or "")
        _draw_round_portrait(
            canvas,
            draw,
            center=(left + x * cell + cell // 2, top + y * cell + cell // 2),
            diameter=max(12, min(cell - 8, 48)),
            actor=actor,
            portrait=portraits.get(actor_id),
            current=actor_id == current_actor_id,
        )
        initiative = str(int(actor.get("initiative", 0) or 0))
        badge_radius = max(7, min(13, cell // 5))
        badge_x = left + (x + 1) * cell - badge_radius
        badge_y = top + y * cell + badge_radius
        draw.ellipse(
            (
                badge_x - badge_radius,
                badge_y - badge_radius,
                badge_x + badge_radius,
                badge_y + badge_radius,
            ),
            fill=_INK,
            outline=_PAPER,
            width=1,
        )
        draw.text(
            (badge_x, badge_y),
            initiative,
            fill=_PAPER,
            font=_font(max(8, badge_radius), bold=True),
            anchor="mm",
        )
        hp = actor.get("hp")
        if isinstance(hp, Mapping) and hp.get("current") is not None and hp.get("max"):
            ratio = max(0.0, min(1.0, float(hp["current"]) / float(hp["max"])))
            bar_left = left + x * cell + 4
            bar_right = left + (x + 1) * cell - 4
            bar_top = top + (y + 1) * cell - 6
            draw.rectangle((bar_left, bar_top, bar_right, bar_top + 3), fill="#3a1712")
            draw.rectangle(
                (bar_left, bar_top, bar_left + int((bar_right - bar_left) * ratio), bar_top + 3),
                fill=_ACCENT,
            )


def _draw_agent_panel(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    body_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    draw.rounded_rectangle(
        (left, top, left + width, top + height),
        radius=18,
        fill=_GRID_BG,
    )
    draw.text(
        (left + width // 2, top + height // 2 - 24),
        "AGENT SPATIAL ADJUDICATION",
        fill=_PAPER,
        font=_font(30, bold=True),
        anchor="mm",
    )
    draw.text(
        (left + width // 2, top + height // 2 + 24),
        "No grid or token coordinates are authoritative in this encounter.",
        fill="#adb2a8",
        font=body_font,
        anchor="mm",
    )


def render_combat_png(
    encounter: Mapping[str, Any],
    *,
    portraits: Mapping[str, bytes] | None = None,
    audience_projection: str,
    party_public_map_asset: bytes | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Render one already audience-filtered encounter to a PNG plus metadata."""

    value = dict(encounter)
    combatants = [
        dict(item) for item in value.get("combatants", []) if isinstance(item, Mapping)
    ]
    current_actor_id = _current_actor_id(combatants, value.get("turn_index"))
    positioning_mode = str(value.get("positioning_mode") or "agent").casefold()
    battle_map = dict(value.get("battle_map") or {})
    bounds = dict(battle_map.get("bounds") or {})
    width_cells = int(bounds.get("width_cells", 0) or 0)
    height_cells = int(bounds.get("height_cells", 0) or 0)
    grid_mode = positioning_mode == "grid" and width_cells > 0 and height_cells > 0
    map_artwork = (
        _party_public_map_image(
            battle_map,
            party_public_map_asset,
            audience_projection=audience_projection,
        )
        if grid_mode
        else None
    )

    cell = max(8, min(64, 1120 // width_cells, 800 // height_cells)) if grid_mode else 0
    grid_width = width_cells * cell if grid_mode else 960
    grid_height = height_cells * cell if grid_mode else 720
    map_width = grid_width + (_COORDINATE_GUTTER if grid_mode else 0)
    map_height = grid_height + (_COORDINATE_GUTTER if grid_mode else 0)
    roster_width = 430
    header_height = 132
    footer_height = 70
    roster_height = max(300, len(combatants) * 78 + 54)
    canvas_width = max(1180, map_width + roster_width + 90)
    canvas_height = max(
        760,
        header_height + map_height + footer_height,
        header_height + roster_height,
    )
    canvas = Image.new("RGB", (canvas_width, canvas_height), _PAPER)
    draw = ImageDraw.Draw(canvas)
    title_font = _font(38, bold=True)
    meta_font = _font(18, bold=True)
    body_font = _font(18)
    small_font = _font(14)

    draw.text((42, 30), str(value.get("name") or "Combat"), fill=_INK, font=title_font)
    round_label = f"ROUND {int(value.get('round', 1) or 1)}"
    mode_label = "GRID POSITIONING" if grid_mode else "AGENT POSITIONING - NO COORDINATES"
    draw.text((44, 82), f"{round_label} - {mode_label}", fill=_ACCENT, font=meta_font)
    draw.text(
        (canvas_width - 42, 46),
        str(audience_projection).upper(),
        fill=_MUTED,
        font=small_font,
        anchor="ra",
    )

    map_left = 42
    map_top = header_height
    portrait_values = portraits or {}
    if grid_mode:
        _draw_grid(
            canvas,
            draw,
            battle_map=battle_map,
            combatants=combatants,
            portraits=portrait_values,
            current_actor_id=current_actor_id,
            left=map_left + _COORDINATE_GUTTER,
            top=map_top + _COORDINATE_GUTTER,
            width_cells=width_cells,
            height_cells=height_cells,
            cell=cell,
            map_artwork=map_artwork,
        )
    else:
        _draw_agent_panel(
            draw,
            left=map_left,
            top=map_top,
            width=map_width,
            height=map_height,
            body_font=body_font,
        )

    roster_left = map_left + map_width + 38
    draw.text((roster_left, map_top), "INITIATIVE", fill=_INK, font=meta_font)
    row_top = map_top + 38
    for actor in sorted(combatants, key=lambda item: -int(item.get("initiative", 0) or 0)):
        actor_id = str(actor.get("actor_id") or "")
        current = actor_id == current_actor_id
        draw.rounded_rectangle(
            (roster_left, row_top, canvas_width - 42, row_top + 66),
            radius=10,
            fill="#f8dfd4" if current else "#fffaf0",
            outline=_ACCENT if current else "#d5cfc2",
            width=2 if current else 1,
        )
        _draw_round_portrait(
            canvas,
            draw,
            center=(roster_left + 36, row_top + 33),
            diameter=44,
            actor=actor,
            portrait=portrait_values.get(actor_id),
            current=current,
        )
        draw.text(
            (roster_left + 70, row_top + 9),
            str(int(actor.get("initiative", 0) or 0)),
            fill=_ACCENT,
            font=meta_font,
        )
        name = _fit_text(
            draw,
            str(actor.get("name") or actor_id),
            meta_font,
            roster_width - 145,
        )
        draw.text((roster_left + 112, row_top + 9), name, fill=_INK, font=meta_font)
        details = []
        position = actor.get("position")
        if isinstance(position, Mapping):
            details.append(f"({position.get('x')}, {position.get('y')})")
        hp = actor.get("hp")
        if isinstance(hp, Mapping) and hp.get("current") is not None:
            details.append(f"HP {hp.get('current')}/{hp.get('max', '?')}")
        details.extend(_condition_labels(actor.get("conditions"))[:3])
        detail_text = _fit_text(
            draw,
            " - ".join(details) or "VISIBLE COMBATANT",
            small_font,
            roster_width - 100,
        )
        draw.text((roster_left + 72, row_top + 40), detail_text, fill=_MUTED, font=small_font)
        row_top += 78

    map_revision = int(battle_map.get("map_revision", 1) or 1) if battle_map else 0
    grid_label = (
        f"MAP REV {map_revision} - {width_cells}x{height_cells} - "
        f"{int(dict(battle_map.get('grid') or {}).get('cell_ft', 5) or 5)} FT CELLS"
        if grid_mode
        else "AGENT MODE - IMAGE CONTAINS NO MECHANICAL GEOMETRY"
    )
    footer_y = canvas_height - 48
    draw.text((42, footer_y), grid_label, fill=_MUTED, font=small_font)
    draw.text(
        (canvas_width - 42, footer_y),
        "SAGASMITH - MCP STATE IS AUTHORITATIVE",
        fill=_MUTED,
        font=small_font,
        anchor="ra",
    )

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    content = output.getvalue()
    actor_summaries = []
    for actor in combatants:
        position = actor.get("position")
        location = (
            f" at {position.get('x')},{position.get('y')}"
            if isinstance(position, Mapping)
            else ""
        )
        actor_summaries.append(
            f"{actor.get('name') or 'Visible combatant'}, initiative "
            f"{int(actor.get('initiative', 0) or 0)}{location}"
        )
    share_card = _share_metadata(
        value,
        combatants=combatants,
        current_actor_id=current_actor_id,
        grid_mode=grid_mode,
        width_cells=width_cells,
        height_cells=height_cells,
        map_revision=map_revision if grid_mode else None,
    )
    public_asset = dict(battle_map.get("party_public_map_asset") or {})
    artwork_alt = (
        f" Map artwork: {public_asset.get('alt_text')}." if map_artwork is not None else ""
    )
    alt_text = _bounded_text_value(
        f"{value.get('name') or 'Combat'}, round {int(value.get('round', 1) or 1)}."
        f"{artwork_alt} " + "; ".join(actor_summaries),
        1800,
    )
    map_asset_metadata: dict[str, Any] = {
        "used": map_artwork is not None,
        "fallback": None if map_artwork is not None else "deterministic_texture",
    }
    if map_artwork is not None:
        artwork, alignment = map_artwork
        target_width = int(alignment["width_cells"]) * cell
        target_height = int(alignment["height_cells"]) * cell
        fitted = ImageOps.contain(artwork, (target_width, target_height))
        map_asset_metadata.update(
            {
                "letterboxed": fitted.size != (target_width, target_height),
                "alt_text": str(public_asset["alt_text"]),
                "license": str(public_asset["license"]),
                "attribution": str(public_asset["attribution"]),
                "grid_alignment": dict(alignment),
            }
        )
    metadata = {
        "encounter_id": value.get("id"),
        "positioning_mode": positioning_mode,
        "round": int(value.get("round", 1) or 1),
        "current_actor_id": current_actor_id,
        "map_revision": map_revision if grid_mode else None,
        "audience_projection": audience_projection,
        "width": canvas_width,
        "height": canvas_height,
        "mime_type": "image/png",
        "image_checksum": hashlib.sha256(content).hexdigest(),
        "alt_text": alt_text,
        "share_card": share_card,
        "suggested_caption": share_card["suggested_caption"],
        "decorative_map_asset": map_asset_metadata,
    }
    return metadata, content


__all__ = ["render_combat_png"]
