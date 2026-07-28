"""D&D-specific enrichment for the system-neutral module parser."""

from __future__ import annotations

import json
import re

from sagasmith_core.modules import GenericModuleProfile, SceneBoundary

_ROOM_CODE_PATTERN = (
    r"(?:(?=[A-Z]{1,3}\s*[0-9IlO]{1,3}[A-Za-z]?\s*[.．。:：-])"
    r"(?=[^.．。:：-]*\d)"
    r"[A-Z]{1,3}\s*[0-9IlO]{1,3}[A-Za-z]?"
    r"|[A-Z]{1,3}\s*[Il][0-9IlO]{0,2}"
    r"|\d(?:\s*\d){0,2}\s*[A-Za-z]?)"
)
_ROOM_TARGET_PATTERN = (
    r"(?:[A-Z]{1,3}\s*[0-9IlO]{1,3}[A-Za-z]?"
    r"|[A-Z]{1,3}\s*[Il][0-9IlO]{0,2}"
    r"|\d(?:\s*\d){0,2}\s*[A-Za-z]?)"
)
_ROOM = re.compile(
    rf"^{_ROOM_CODE_PATTERN}\s*[.．。:：-]\s*(?=[^\W_])\S",
    re.IGNORECASE,
)
_STAT_SIGNALS = (
    "armor class",
    "hit points",
    "speed",
    "damage immunities",
    "condition immunities",
    "actions",
    "护甲等级",
    "生命值",
    "速度",
    "伤害免疫",
    "状态免疫",
    "动作",
)
_KEYWORDS = {
    "trap": ("trap", "陷阱"),
    "npc": ("npc", "非玩家角色"),
    "monster": ("monster", "怪物"),
    "reward": ("reward", "treasure", "奖励", "宝藏"),
    "encounter": ("encounter", "遭遇"),
    "clue": ("clue", "线索"),
}
_COMBAT_SUBSECTION_SIGNALS = ("战斗", "遭遇", "陷阱", "推销", "巡逻")
_LOCATION_TITLE_SIGNALS = (
    "alcove",
    "bridge",
    "cave",
    "cellar",
    "chamber",
    "chapel",
    "corridor",
    "courtyard",
    "crypt",
    "dungeon",
    "gate",
    "hall",
    "hideout",
    "house",
    "inn",
    "keep",
    "kitchen",
    "lair",
    "library",
    "manor",
    "orchard",
    "passage",
    "provisions",
    "room",
    "shrine",
    "shop",
    "store",
    "tavern",
    "tap house",
    "taphouse",
    "temple",
    "tower",
    "vault",
    "villa",
    "windmill",
    "estate",
    "coster",
    "exchange",
    "farm",
    "洞",
    "厅",
    "地窖",
    "墓",
    "室",
    "庭院",
    "神殿",
    "通道",
)
_CJK_RANGES = (("一", "鿿"), ("㐀", "䶿"), ("豈", "﫿"))
_LOCATION_BODY_SIGNAL = re.compile(
    r"\b(?:when|as)\s+(?:the\s+)?characters\s+"
    r"(?:approach|arrive(?:\s+at)?|enter|reach|visit)\b",
    re.IGNORECASE,
)
_ACTION_SCENE_BODY_SIGNAL = re.compile(
    r"\b(?:"
    r"a\s+successful\s+dc\s+\d+\b[^.\n]{0,120}\bcheck\b"
    r"|(?:the\s+)?characters\b[^.\n]{0,120}"
    r"\b(?:can|must|need\s+to|try\s+to)\b"
    r"|allows\s+(?:the\s+)?characters\s+to\b"
    r"|visits?\s+the\s+characters['’]\s+(?:residence|home)\b"
    r"[^.\n]{0,120}\binvites?\s+them\b"
    r")",
    re.IGNORECASE,
)
_ACTION_SCENE_TITLE_SIGNALS = (
    "chase",
    "pursuit",
)
_NON_LOCATION_HEADINGS = {
    "adventure conclusion",
    "aftermath",
    "developments",
    "finding floon",
    "getting involved",
    "hanging back",
    "level advancement",
    "roleplaying",
    "treasure",
    "where to start",
}
_DIMENSIONS = re.compile(
    r"(?P<width>\d{1,3})\s*(?:(?:-?foot|feet|ft\.?|\u5c3a)\s*)?"
    r"(?:by|x|\u00d7|\u4e58)\s*"
    r"(?P<height>\d{1,3})\s*(?:-?foot|feet|ft\.?|\u5c3a)",
    re.IGNORECASE,
)
_ROOM_CODE = re.compile(
    rf"^(?P<code>{_ROOM_CODE_PATTERN})"
    r"\s*[.．。:：-]",
    re.IGNORECASE,
)
_ROOM_HEADING = re.compile(
    rf"^(?:#{{1,6}}\s+)?(?P<code>{_ROOM_CODE_PATTERN})"
    r"\s*[.．。:：-]",
    re.IGNORECASE | re.MULTILINE,
)


_ROOM_TITLE_LINE = re.compile(
    rf"^(?P<marker>#{{1,6}}\s+)?"
    rf"(?P<title>(?P<code>{_ROOM_CODE_PATTERN})"
    r"\s*[.\uFF0E\u3002\uFF61\uFF1A:]\s*(?=[^\W_])\S[^\r\n]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _is_reference_chapter(title: str) -> bool:
    folded = title.casefold().strip()
    return bool(
        folded in {"front matter", "rules index", "chapter intro"}
        or re.match(r"^(?:app(?:endix)?\s*\.?|附录)", folded)
    )


def _contains_location_title_signal(folded_title: str) -> bool:
    if any(signal in folded_title for signal in _LOCATION_TITLE_SIGNALS):
        return True
    # Display-font extraction often splits an initial or interior capital
    # (``H OUSE``, ``T EMPLE``). Accept whitespace inside a single known
    # location word without compacting across ordinary word boundaries.
    return any(
        re.search(
            r"\b" + r"\s*".join(re.escape(char) for char in signal) + r"\b",
            folded_title,
        )
        for signal in _LOCATION_TITLE_SIGNALS
        if re.fullmatch(r"[a-z]{4,}", signal)
    )


def _contains_action_scene_title_signal(folded_title: str) -> bool:
    """Recognize authored action headings despite display-font OCR spacing."""
    return any(
        re.search(
            r"\b" + r"\s*".join(re.escape(char) for char in signal) + r"\b",
            folded_title,
        )
        for signal in _ACTION_SCENE_TITLE_SIGNALS
    )


def _location_heading_kind(title: str, body: str = "") -> str | None:
    text = title.strip()
    folded = text.casefold()
    normalized_body = re.sub(
        r"(?<=[A-Za-z])[\x00-\x1f\x7f-\x9f\u00ad](?=[A-Za-z])",
        "",
        body,
    )
    if not _looks_like_scene_heading(text):
        return None
    physical_location = bool(
        _ROOM.match(text)
        or (
            1 <= len(text.split()) <= 10
            and _contains_location_title_signal(folded)
        )
        or (
            folded not in _NON_LOCATION_HEADINGS
            and 1 <= len(text.split()) <= 6
            and any(char.isalpha() for char in text)
            and text.upper() == text
            and bool(_LOCATION_BODY_SIGNAL.search(normalized_body))
        )
    )
    if physical_location:
        return "room"
    if (
        folded not in _NON_LOCATION_HEADINGS
        and 1 <= len(text.split()) <= 6
        and any(char.isalpha() for char in text)
        and text.upper() == text
        and (
            _contains_action_scene_title_signal(folded)
            or bool(_ACTION_SCENE_BODY_SIGNAL.search(normalized_body))
        )
    ):
        return "scene"
    return None


def _looks_like_location_heading(title: str, body: str = "") -> bool:
    return _location_heading_kind(title, body) is not None


def _looks_like_scene_heading(title: str) -> bool:
    """Reject visual-font fragments while preserving short authored headings."""
    text = re.sub(r"\s+", " ", title).strip()
    words = re.findall(r"[A-Za-z][A-Za-z'’.-]*|[\u4e00-\u9fff]+", text)
    alphanumeric = sum(char.isalnum() for char in text)
    if not text or len(text) > 110 or len(words) > 16:
        return False
    if alphanumeric / max(len(text), 1) < 0.45:
        return False
    if re.match(r"^[a-z]", text) or re.match(r"^(?:By|由)\s+\S+", text):
        return False
    if re.match(
        r"^[I|]\s*[\"'“”‘’]?\s*(?:A|An|The|This|These|You|Your|Behind|In)\b",
        text,
    ):
        return False
    if re.match(
        r"^(?:[I1l|]\s+){1,3}(?:CHAPTER|ENCOUNTER)\b",
        text,
        re.IGNORECASE,
    ):
        # Flow-chart connectors are commonly extracted as headings such as
        # ``1 l ENCOUNTER 3,``. They are diagram labels, not scene boundaries,
        # and otherwise collapse the surrounding authored encounter headings
        # into one oversized scene.
        return False
    if re.match(r"^(?:Ch(?:apter)?|App(?:endix)?)\b", text, re.IGNORECASE):
        return False
    if re.match(r"^[A-Z]\s+(?:CHAPTER|ENCOUNTER)\b", text, re.IGNORECASE):
        return False
    if re.search(r"[•~_=]{1,}", text) and alphanumeric < 8:
        return False
    if len(words) >= 8 and re.search(r"[,.;。；，]$", text):
        return False
    coded = _ROOM.match(text)
    if coded and len(words) >= 10:
        return False
    return True


def _is_diagram_overview(title: str) -> bool:
    """Recognize authored flow-chart/index sections that are not spatial scenes."""

    compact = re.sub(r"[^a-z0-9]+", "", title.casefold())
    return compact in {
        "adventureflowchart",
        "encounterchainsbyseason",
    }


_EXPLICIT_ROUTE_PATTERNS = (
    re.compile(
        r"(?:通向|通往|连接到|连接至|直达)\s*(?:了|着)?\s*"
        rf"(?:区域|区|房间)?\s*(?P<target>{_ROOM_TARGET_PATTERN})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:leads?|connects?|opens?|descends?|ascends?)\s+"
        r"(?:directly\s+)?(?:to|into)\s+(?:(?:area|room)\s+)?"
        rf"(?P<target>{_ROOM_TARGET_PATTERN})\b",
        re.IGNORECASE,
    ),
)
_RUNTIME_MANIFEST = re.compile(
    r"<!--\s*sagasmith-runtime-manifest\s*(?P<body>\{.*?\})\s*-->",
    re.IGNORECASE | re.DOTALL,
)
_MANIFEST_ID = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,199}$")
_MANIFEST_COLLECTIONS = (
    "entities",
    "secrets",
    "clues",
    "plot_nodes",
    "foreshadowing",
    "branches",
)


def _runtime_manifest_metadata(content: str) -> dict[str, object]:
    matches = list(_RUNTIME_MANIFEST.finditer(content))
    if not matches:
        return {}
    errors: list[str] = []
    if len(matches) > 1:
        errors.append("module must contain at most one runtime manifest")
    try:
        manifest = json.loads(matches[0].group("body"))
    except json.JSONDecodeError as exc:
        return {"runtime_manifest_errors": [f"runtime manifest is invalid JSON: {exc.msg}"]}
    if not isinstance(manifest, dict):
        return {"runtime_manifest_errors": ["runtime manifest must be an object"]}
    if manifest.get("schema_version") != 1:
        errors.append("runtime manifest schema_version must be 1")
    module_key = manifest.get("module_key")
    if not isinstance(module_key, str) or not _MANIFEST_ID.fullmatch(module_key):
        errors.append("runtime manifest module_key must be a stable lowercase id")

    seen: set[str] = set()
    for collection in _MANIFEST_COLLECTIONS:
        values = manifest.get(collection, [])
        if not isinstance(values, list):
            errors.append(f"runtime manifest {collection} must be a list")
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                errors.append(f"runtime manifest {collection}[{index}] must be an object")
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not _MANIFEST_ID.fullmatch(item_id):
                errors.append(
                    f"runtime manifest {collection}[{index}].id must be a stable lowercase id"
                )
                continue
            if item_id in seen:
                errors.append(f"runtime manifest contains duplicate id: {item_id}")
            seen.add(item_id)
            if collection == "secrets" and not isinstance(item.get("initial_knowers", []), list):
                errors.append(
                    f"runtime manifest secrets[{index}].initial_knowers must be a list"
                )
            if collection in {"clues", "plot_nodes", "branches"} and not item.get(
                "trigger"
            ):
                errors.append(f"runtime manifest {collection}[{index}].trigger is required")
            if collection in {"plot_nodes", "branches"} and not isinstance(
                item.get("consequences", []), list
            ):
                errors.append(
                    f"runtime manifest {collection}[{index}].consequences must be a list"
                )
    return {"runtime_manifest": manifest, "runtime_manifest_errors": errors}


def _has_cjk(text: str) -> bool:
    return any(low <= char <= high for low, high in _CJK_RANGES for char in text)


def _has_ascii_alpha(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]{2,}", text))


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _preamble_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip():
            return stripped.lstrip("#").strip()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("<!--"):
            return stripped[:80]
    return "Chapter Intro"


def _scene_tags(title: str) -> list[str]:
    folded = title.casefold()
    groups = (
        ("intro", ("运作", "运行", "running the", "how to", "running this", "about this")),
        (
            "combat",
            (
                "战斗",
                "遭遇",
                "冲突",
                "攻击",
                "伏击",
                "battle",
                "fight",
                "combat",
                "ambush",
                "assault",
                "skirmish",
            ),
        ),
        (
            "dungeon",
            (
                "大厅",
                "地城",
                "教堂",
                "墓",
                "要塞",
                "堡垒",
                "塔",
                "神殿",
                "墓穴",
                "dungeon",
                "temple",
                "keep",
                "fort",
                "castle",
                "tower",
                "cathedral",
                "crypt",
            ),
        ),
        (
            "transition",
            (
                "逃出",
                "离开",
                "前往",
                "穿越",
                "旅行",
                "出发",
                "escape",
                "depart",
                "travel",
                "journey",
                "road",
                "toward",
                "leave",
            ),
        ),
        (
            "social",
            (
                "小镇",
                "村庄",
                "城市",
                "旅馆",
                "市场",
                "广场",
                "港口",
                "酒馆",
                "town",
                "village",
                "city",
                "tavern",
                "inn",
                "market",
                "harbor",
                "square",
            ),
        ),
    )
    for tag, signals in groups:
        if any(signal in folded for signal in signals):
            if tag == "combat":
                return ["combat", "encounter"]
            if tag == "dungeon":
                return ["exploration", "dungeon"]
            if tag == "social":
                return ["exploration", "social"]
            return [tag]
    return ["exploration"]


def _normalized_room_code(value: str) -> str:
    compact = re.sub(r"\s+", "", value).casefold()
    matched = re.fullmatch(
        r"(?P<prefix>[a-z]{1,3}?)(?P<number>[0-9ilo]{1,3})(?P<suffix>[a-z]?)",
        compact,
    )
    if matched is None:
        return compact
    number = matched.group("number").translate(str.maketrans({"i": "1", "l": "1", "o": "0"}))
    return f"{matched.group('prefix')}{number}{matched.group('suffix')}"


def _location_key(title: str, ordinal: int) -> str:
    """Produce a stable-enough key from parser evidence, never a display label."""
    source = title
    matched = _ROOM_CODE.match(title.strip())
    if matched is not None:
        source = f"{_normalized_room_code(matched.group('code'))} {title.strip()[matched.end():]}"
    folded = re.sub(r"[^a-z0-9]+", "-", source.casefold()).strip("-")
    return folded[:72] or f"location-{ordinal + 1}"


def _explicit_connections(
    text: str, locations: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Extract only prose that explicitly states one room leads to another.

    Room-number order and generic cross-references are deliberately ignored: an
    encounter in D2 mentioning reinforcements from D4 is not enough evidence of
    a traversable D2-D4 edge. Each accepted edge retains its source line so a DM
    or importer UI can audit the parser decision.
    """
    key_by_code: dict[str, str] = {}
    for location in locations:
        matched = _ROOM_CODE.match(str(location.get("title") or "").strip())
        if matched:
            key_by_code[_normalized_room_code(matched.group("code"))] = str(
                location["key"]
            )
    if len(key_by_code) < 2:
        return []

    headings = list(_ROOM_HEADING.finditer(text))
    connections: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for index, heading in enumerate(headings):
        source_code = _normalized_room_code(heading.group("code"))
        source_key = key_by_code.get(source_code)
        if source_key is None:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end() : end]
        for pattern in _EXPLICIT_ROUTE_PATTERNS:
            for route in pattern.finditer(section):
                target_key = key_by_code.get(
                    _normalized_room_code(route.group("target"))
                )
                if target_key is None or target_key == source_key:
                    continue
                edge = tuple(sorted((source_key, target_key)))
                if edge in seen:
                    continue
                seen.add(edge)
                matched_text = route.group(0).strip()
                connections.append(
                    {
                        "from": source_key,
                        "to": target_key,
                        "bidirectional": True,
                        "kind": "passage",
                        "confidence": "explicit_text",
                        "evidence": {
                            "line": _line_number(text, heading.end() + route.start()),
                            "text": matched_text,
                        },
                    }
                )
    return connections


def _spatial_manifest(
    title: str,
    text: str,
    subsections: list[dict[str, object]],
    *,
    reference: bool = False,
    allow_fallback: bool = True,
) -> dict[str, object]:
    """Emit conservative scene-space evidence; it is not an inferred battle map."""
    if reference:
        return {
            "schema_version": 1,
            "grid": {"kind": "square", "cell_ft": 5},
            "locations": [],
            "connections": [],
        }
    locations: list[dict[str, object]] = []
    location_key_counts: dict[str, int] = {}
    scene_title_tokens = set(re.findall(r"[a-z0-9]+", title.casefold()))
    if _ROOM.match(title.strip()):
        dimensions = _DIMENSIONS.search(text)
        scene_key = _location_key(title, 0)
        location_key_counts[scene_key] = 1
        locations.append(
            {
                "key": scene_key,
                "title": title,
                "kind": "room",
                "dimensions_ft": (
                    {
                        "width": int(dimensions.group("width")),
                        "height": int(dimensions.group("height")),
                    }
                    if dimensions
                    else None
                ),
                "confidence": "explicit_heading",
            }
        )
    for ordinal, item in enumerate(subsections):
        location_kind = str(item.get("type") or "")
        if location_kind not in {"room", "scene"}:
            continue
        label = str(item["title"])
        label_tokens = set(re.findall(r"[a-z0-9]+", label.casefold()))
        if label_tokens == scene_title_tokens:
            continue
        if (
            not _ROOM.match(label)
            and len(label_tokens) == 1
            and label_tokens <= scene_title_tokens
        ):
            continue
        base_key = _location_key(label, ordinal)
        location_key_counts[base_key] = location_key_counts.get(base_key, 0) + 1
        occurrence = location_key_counts[base_key]
        location_key = (
            base_key
            if occurrence == 1
            else f"{base_key[: max(1, 71 - len(str(occurrence)))]}-{occurrence}"
        )
        locations.append(
            {
                "key": location_key,
                "title": label,
                "kind": location_kind,
                "line": item.get("line"),
                "dimensions_ft": item.get("dimensions_ft"),
                "confidence": "explicit_heading",
            }
        )
    # PDF-to-Markdown conversion can preserve an authored numbered room title
    # as its own text line while dropping only the Markdown heading marker.
    # Recover those exact labels without inferring room order or connectivity.
    room_title_matches = list(_ROOM_TITLE_LINE.finditer(text))
    existing_codes = {
        _normalized_room_code(matched.group("code"))
        for location in locations
        if (matched := _ROOM_CODE.match(str(location.get("title") or "").strip()))
    }
    if existing_codes or len(room_title_matches) >= 2:
        for match_index, matched_title in enumerate(room_title_matches):
            code = _normalized_room_code(matched_title.group("code"))
            if code in existing_codes:
                continue
            label = matched_title.group("title").strip()
            section_end = (
                room_title_matches[match_index + 1].start()
                if match_index + 1 < len(room_title_matches)
                else len(text)
            )
            section = text[matched_title.end() : section_end]
            dimensions = _DIMENSIONS.search(section)
            base_key = _location_key(label, len(locations))
            location_key_counts[base_key] = location_key_counts.get(base_key, 0) + 1
            occurrence = location_key_counts[base_key]
            location_key = (
                base_key
                if occurrence == 1
                else f"{base_key[: max(1, 71 - len(str(occurrence)))]}-{occurrence}"
            )
            locations.append(
                {
                    "key": location_key,
                    "title": label,
                    "kind": "room",
                    "line": _line_number(text, matched_title.start()),
                    "dimensions_ft": (
                        {
                            "width": int(dimensions.group("width")),
                            "height": int(dimensions.group("height")),
                        }
                        if dimensions
                        else None
                    ),
                    "confidence": (
                        "explicit_heading"
                        if matched_title.group("marker")
                        else "explicit_text_heading"
                    ),
                }
            )
            existing_codes.add(code)
    locations.sort(
        key=lambda location: (
            int(location.get("line") or 0),
            str(location.get("key") or ""),
        )
    )
    if not locations and not allow_fallback:
        return {
            "schema_version": 1,
            "grid": {"kind": "square", "cell_ft": 5},
            "locations": [],
            "connections": [],
        }
    if not locations:
        dimensions = _DIMENSIONS.search(text)
        location_heading = bool(_ROOM.match(title.strip()))
        locations.append(
            {
                "key": _location_key(title, 0),
                "title": title,
                "kind": "room" if location_heading else "scene",
                "dimensions_ft": (
                    {
                        "width": int(dimensions.group("width")),
                        "height": int(dimensions.group("height")),
                    }
                    if dimensions
                    else None
                ),
                "confidence": (
                    "explicit_heading" if location_heading else "scene_fallback"
                ),
            }
        )
    return {
        "schema_version": 1,
        "grid": {"kind": "square", "cell_ft": 5},
        "locations": locations,
        # Heading order remains unsafe. Only explicit route prose is accepted.
        "connections": _explicit_connections(text, locations),
    }


class DndModuleProfile(GenericModuleProfile):
    name = "dnd5e"
    version = "28"

    def document_metadata(self, content: str) -> dict[str, object]:
        """Parse and validate the optional generated-module runtime manifest."""
        return _runtime_manifest_metadata(content)

    def classify_chunk(self, heading: str, text: str) -> str:
        if _ROOM.match(heading):
            return "room"
        folded = text.casefold()
        if sum(signal in folded for signal in _STAT_SIGNALS) >= 2:
            return "statblock"
        if any(value in folded for value in _KEYWORDS["encounter"]):
            return "encounter"
        return super().classify_chunk(heading, text)

    def keywords(self, title: str, text: str) -> list[str]:
        values = super().keywords(title, text)
        folded = f"{title}\n{text}".casefold()
        for key, signals in _KEYWORDS.items():
            if any(signal in folded for signal in signals):
                values.append(key)
        return list(dict.fromkeys(values))

    def scene_boundaries(
        self,
        chapter_title: str,
        chapter_content: str,
    ) -> list[SceneBoundary]:
        headings = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", chapter_content, re.MULTILINE))
        plausible_headings = [
            heading
            for heading in headings
            if _looks_like_scene_heading(heading.group(2))
        ]
        counts = {
            level: sum(len(match.group(1)) == level for match in plausible_headings)
            for level in (2, 3, 4)
        }
        if counts[2] and counts[3] >= counts[2] * 5:
            scene_level = 3
        elif counts[2]:
            scene_level = 2
        elif counts[3]:
            scene_level = 3
        else:
            scene_level = 4
        sub_level = scene_level + 1 if scene_level < 6 else None
        room_level = scene_level + 2 if scene_level < 5 else None
        scene_headings = [
            heading
            for heading in plausible_headings
            if len(heading.group(1)) == scene_level
        ]
        reference_chapter = _is_reference_chapter(chapter_title)
        if not scene_headings:
            return [
                SceneBoundary(
                    "Chapter Content",
                    0,
                    len(chapter_content),
                    {
                        "scene_type": "section",
                        "scene_level": scene_level,
                        "subsections": [],
                        "headings": [],
                        "tags": ["exploration"],
                        "reference": reference_chapter,
                        "spatial": _spatial_manifest(
                            chapter_title,
                            chapter_content,
                            [],
                            reference=reference_chapter,
                        ),
                        "line_count": max(1, len(chapter_content.splitlines())),
                    },
                )
            ]

        boundaries: list[SceneBoundary] = []
        first_start = scene_headings[0].start()
        if chapter_content[:first_start].strip():
            preamble = chapter_content[:first_start]
            preamble_subsections = self._subsections(
                headings,
                0,
                first_start,
                sub_level,
                room_level,
                chapter_content,
            )
            boundaries.append(
                SceneBoundary(
                    _preamble_title(preamble),
                    0,
                    first_start,
                    {
                        "scene_type": "reference" if reference_chapter else "overview",
                        "scene_level": scene_level,
                        "subsections": preamble_subsections,
                        "headings": [],
                        "tags": (
                            ["reference"]
                            if reference_chapter
                            else _scene_tags(_preamble_title(preamble))
                        ),
                        "reference": reference_chapter,
                        "spatial": _spatial_manifest(
                            _preamble_title(preamble),
                            preamble,
                            preamble_subsections,
                            reference=reference_chapter,
                            allow_fallback=False,
                        ),
                        "line_count": max(1, len(preamble.splitlines())),
                    },
                )
            )

        for index, heading in enumerate(scene_headings):
            end = (
                scene_headings[index + 1].start()
                if index + 1 < len(scene_headings)
                else len(chapter_content)
            )
            title = heading.group(2).strip()
            diagram_overview = _is_diagram_overview(title)
            subsections = self._subsections(
                headings,
                heading.start(),
                end,
                sub_level,
                room_level,
                chapter_content,
            )
            tags = ["reference"] if reference_chapter else _scene_tags(title)
            if (
                any(
                    any(signal in str(item["title"]) for signal in _COMBAT_SUBSECTION_SIGNALS)
                    for item in subsections
                )
                and "combat" not in tags
            ):
                tags.append("combat")
            boundaries.append(
                SceneBoundary(
                    title,
                    heading.start(),
                    end,
                    {
                        "scene_type": (
                            "reference"
                            if reference_chapter
                            else "overview"
                            if diagram_overview
                            else "section"
                        ),
                        "scene_level": scene_level,
                        "subsections": subsections,
                        "headings": [str(item["title"]) for item in subsections],
                        "tags": tags,
                        "reference": reference_chapter,
                        "spatial": _spatial_manifest(
                            title,
                            chapter_content[heading.start() : end],
                            subsections,
                            reference=reference_chapter or diagram_overview,
                        ),
                        "line_count": max(
                            1,
                            _line_number(chapter_content, end)
                            - _line_number(chapter_content, heading.start())
                            + 1,
                        ),
                    },
                )
            )
        return self._merge_bilingual(boundaries, chapter_content)

    @staticmethod
    def _subsections(
        headings: list[re.Match[str]],
        start: int,
        end: int,
        sub_level: int | None,
        room_level: int | None,
        content: str,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for heading in headings:
            if not start < heading.start() < end:
                continue
            level = len(heading.group(1))
            item: dict[str, object] | None = None
            title = heading.group(2).strip()
            location_level = sub_level if sub_level is not None else room_level
            next_boundary = next(
                (
                    candidate.start()
                    for candidate in headings
                    if heading.start() < candidate.start() < end
                    and len(candidate.group(1)) <= level
                    and _looks_like_scene_heading(candidate.group(2).strip())
                ),
                end,
            )
            section_body = content[heading.end() : next_boundary]
            location_kind = (
                _location_heading_kind(title, section_body)
                if location_level is not None and level >= location_level
                else None
            )
            if (
                location_level is not None
                and level >= location_level
                and location_kind is not None
            ):
                dimensions = _DIMENSIONS.search(section_body)
                item = {
                    "title": title,
                    "line": _line_number(content, heading.start()),
                    "type": location_kind,
                }
                if dimensions:
                    item["dimensions_ft"] = {
                        "width": int(dimensions.group("width")),
                        "height": int(dimensions.group("height")),
                    }
            elif sub_level is not None and level == sub_level:
                item = {
                    "title": heading.group(2).strip(),
                    "line": _line_number(content, heading.start()),
                    "type": "section",
                }
            if item is not None:
                result.append(item)
        return result

    @staticmethod
    def _merge_bilingual(
        boundaries: list[SceneBoundary],
        content: str,
    ) -> list[SceneBoundary]:
        merged: list[SceneBoundary] = []
        index = 0
        while index < len(boundaries):
            current = boundaries[index]
            line_count = len(content[current.start : current.end].splitlines())
            if line_count <= 2 and index + 1 < len(boundaries):
                following = boundaries[index + 1]
                complementary = (
                    _has_cjk(current.title)
                    and not _has_ascii_alpha(current.title)
                    and _has_ascii_alpha(following.title)
                    and not _has_cjk(following.title)
                ) or (
                    _has_ascii_alpha(current.title)
                    and not _has_cjk(current.title)
                    and _has_cjk(following.title)
                    and not _has_ascii_alpha(following.title)
                )
                if complementary:
                    metadata = {
                        **following.metadata,
                        "subsections": [
                            *current.metadata.get("subsections", []),
                            *following.metadata.get("subsections", []),
                        ],
                        "headings": [
                            *current.metadata.get("headings", []),
                            *following.metadata.get("headings", []),
                        ],
                        "tags": list(
                            dict.fromkeys(
                                [
                                    *current.metadata.get("tags", []),
                                    *following.metadata.get("tags", []),
                                ]
                            )
                        ),
                        "line_count": max(
                            1,
                            _line_number(content, following.end)
                            - _line_number(content, current.start)
                            + 1,
                        ),
                    }
                    current = SceneBoundary(
                        f"{current.title} {following.title}",
                        current.start,
                        following.end,
                        metadata,
                    )
                    index += 1
            merged.append(current)
            index += 1
        return merged
