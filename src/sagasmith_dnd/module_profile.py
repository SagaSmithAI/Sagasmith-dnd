"""D&D-specific enrichment for the system-neutral module parser."""

from __future__ import annotations

import re

from sagasmith_core.modules import GenericModuleProfile, SceneBoundary

_ROOM = re.compile(
    r"^(?:(?:[A-Z]{1,3}\s*[0-9IlO]{1,3})|(?:\d{1,3}\s*[A-Za-z]?))"
    r"\s*[.．。:：-]\s*\S",
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
    "keep",
    "kitchen",
    "lair",
    "library",
    "passage",
    "room",
    "shrine",
    "temple",
    "tower",
    "vault",
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
_DIMENSIONS = re.compile(
    r"(?P<width>\d{1,3})\s*(?:(?:-?foot|feet|ft\.?|\u5c3a)\s*)?"
    r"(?:by|x|\u00d7|\u4e58)\s*"
    r"(?P<height>\d{1,3})\s*(?:-?foot|feet|ft\.?|\u5c3a)",
    re.IGNORECASE,
)
_ROOM_CODE = re.compile(
    r"^(?P<code>(?:[A-Z]{1,3}\s*[0-9IlO]{1,3})|(?:\d{1,3}\s*[A-Za-z]?))"
    r"\s*[.．。:：-]",
    re.IGNORECASE,
)
_ROOM_HEADING = re.compile(
    r"^#{1,6}\s+(?P<code>(?:[A-Z]{1,3}\s*[0-9IlO]{1,3})|"
    r"(?:\d{1,3}\s*[A-Za-z]?))"
    r"\s*[.．。:：-]",
    re.IGNORECASE | re.MULTILINE,
)


def _is_reference_chapter(title: str) -> bool:
    folded = title.casefold().strip()
    return bool(
        folded in {"front matter", "rules index", "chapter intro"}
        or re.match(r"^(?:app(?:endix)?\s*\.?|附录)", folded)
    )


def _looks_like_location_heading(title: str) -> bool:
    text = title.strip()
    folded = text.casefold()
    return bool(
        _ROOM.match(text)
        or (
            1 <= len(text.split()) <= 10
            and any(signal in folded for signal in _LOCATION_TITLE_SIGNALS)
        )
    )


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
_EXPLICIT_ROUTE_PATTERNS = (
    re.compile(
        r"(?:通向|通往|连接到|连接至|直达)\s*(?:了|着)?\s*"
        r"(?:区域|区|房间)?\s*(?P<target>[A-Z]{1,3}\d+[A-Za-z]?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:leads?|connects?|opens?|descends?|ascends?)\s+"
        r"(?:directly\s+)?(?:to|into)\s+(?:area|room\s+)?"
        r"(?P<target>[A-Z]{1,3}\d+[A-Za-z]?)\b",
        re.IGNORECASE,
    ),
)


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


def _location_key(title: str, ordinal: int) -> str:
    """Produce a stable-enough key from parser evidence, never a display label."""
    folded = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
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
            key_by_code[matched.group("code").casefold()] = str(location["key"])
    if len(key_by_code) < 2:
        return []

    headings = list(_ROOM_HEADING.finditer(text))
    connections: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for index, heading in enumerate(headings):
        source_code = heading.group("code").casefold()
        source_key = key_by_code.get(source_code)
        if source_key is None:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end() : end]
        for pattern in _EXPLICIT_ROUTE_PATTERNS:
            for route in pattern.finditer(section):
                target_key = key_by_code.get(route.group("target").casefold())
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
    if reference or not allow_fallback:
        return {
            "schema_version": 1,
            "grid": {"kind": "square", "cell_ft": 5},
            "locations": [],
            "connections": [],
        }
    locations: list[dict[str, object]] = []
    for ordinal, item in enumerate(subsections):
        if item.get("type") != "room":
            continue
        label = str(item["title"])
        locations.append(
            {
                "key": _location_key(label, ordinal),
                "title": label,
                "kind": "room",
                "line": item.get("line"),
                "dimensions_ft": item.get("dimensions_ft"),
                "confidence": "explicit_heading",
            }
        )
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
    version = "7"

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
        sub_level = scene_level + 1 if scene_level < 4 else None
        room_level = scene_level + 2 if scene_level < 3 else None
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
            boundaries.append(
                SceneBoundary(
                    _preamble_title(preamble),
                    0,
                    first_start,
                    {
                        "scene_type": "reference" if reference_chapter else "overview",
                        "scene_level": scene_level,
                        "subsections": self._subsections(
                            headings,
                            0,
                            first_start,
                            sub_level,
                            room_level,
                            chapter_content,
                        ),
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
                            [],
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
                        "scene_type": "reference" if reference_chapter else "section",
                        "scene_level": scene_level,
                        "subsections": subsections,
                        "headings": [str(item["title"]) for item in subsections],
                        "tags": tags,
                        "reference": reference_chapter,
                        "spatial": _spatial_manifest(
                            title,
                            chapter_content[heading.start() : end],
                            subsections,
                            reference=reference_chapter,
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
            if (
                room_level is not None
                and level == room_level
                and _looks_like_location_heading(title)
            ):
                next_boundary = next(
                    (
                        candidate.start()
                        for candidate in headings
                        if heading.start() < candidate.start() < end
                        and len(candidate.group(1)) <= level
                    ),
                    end,
                )
                dimensions = _DIMENSIONS.search(content[heading.end() : next_boundary])
                item = {
                    "title": title,
                    "line": _line_number(content, heading.start()),
                    "type": "room",
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
