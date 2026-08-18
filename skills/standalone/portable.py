#!/usr/bin/env python3
"""D&D 5e Standalone runtime — zero pip dependencies, file-based persistence.

Usage:
    python tools/portable.py doctor
    python tools/portable.py campaign start --name "Campaign" [--edition 2024]
    python tools/portable.py campaign list
    python tools/portable.py campaign get --campaign <id>
    python tools/portable.py character create --campaign <id> --name "Aragorn" [--sheet '{"hp":10}']
    python tools/portable.py character list --campaign <id>
    python tools/portable.py character get --campaign <id> --name "Aragorn"
    python tools/portable.py module ingest --campaign <id> --path <file.md> [--title "Module"]
    python tools/portable.py module index --campaign <id>
    python tools/portable.py module current --campaign <id> [--scope party]
    python tools/portable.py module read-scene --campaign <id> --scene <scene-id>
    python tools/portable.py module search --campaign <id> --query "<text>"
    python tools/portable.py module set-progress --campaign <id> --scene <scene-id> [--scope party] [--progress 50] [--room "Room"] [--state '{}'] [--status current]
    python tools/portable.py rules search --campaign <id> --query "<text>"
    python tools/portable.py roll dice --expression "2d6+3"
    python tools/portable.py roll check --dc 15 --score 16 [--advantage] [--proficient] [--level 5]
    python tools/portable.py roll attack --dc 17 --score 18 [--proficient] [--level 5]
    python tools/portable.py event add --campaign <id> --type combat --summary "Fought orcs" [--payload '{}']
    python tools/portable.py event list --campaign <id>
    python tools/portable.py memory upsert --campaign <id> \
        --fact-key location:mat:key --type fact --subject "Key" \
        --content "The key is under the mat" --expected-revision 0
    python tools/portable.py memory list --campaign <id>
    python tools/portable.py memory search --campaign <id> --query "<text>"
    python tools/portable.py memory history --campaign <id> --fact-key location:mat:key
    python tools/portable.py save create --campaign <id> --label "Before boss"
    python tools/portable.py save list --campaign <id>
    python tools/portable.py save restore --campaign <id> --slot <slot>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

# ── data root ───────────────────────────────────────────────────────
_DATA_ROOT = Path.home() / ".sagasmith"
_CAMPAIGNS_INDEX = _DATA_ROOT / "campaigns.json"
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_LATIN_WORD = re.compile(r"[A-Za-z0-9_'-]+")
_SPECIAL = re.compile(r"[*^\"()+\-\\]")


def _data_dir(campaign_id: str) -> Path:
    return _DATA_ROOT / campaign_id


def _ensure_data() -> None:
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if not _CAMPAIGNS_INDEX.exists():
        _CAMPAIGNS_INDEX.write_text("{}", encoding="utf-8")


def _load_index() -> dict[str, Any]:
    _ensure_data()
    return json.loads(_CAMPAIGNS_INDEX.read_text(encoding="utf-8"))


def _save_index(index: dict[str, Any]) -> None:
    _atomic_write(_CAMPAIGNS_INDEX, json.dumps(index, ensure_ascii=False, indent=2))


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_line(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _scene_id(source_key: str, chapter_ordinal: int, scene_ordinal: int) -> str:
    return f"{source_key}__ch{chapter_ordinal}_sc{scene_ordinal}"


# ── query expansion ─────────────────────────────────────────────────
_TTRPG_TERMS: dict[str, list[str]] = {
    "豁免": ["save", "saving"],
    "检定": ["check", "roll", "test"],
    "属性": ["ability", "score", "stat"],
    "技能": ["skill"],
    "熟练": ["proficient", "proficiency"],
    "攻击": ["attack", "strike"],
    "伤害": ["damage", "wound", "hurt"],
    "防御": ["defense", "armor", "protect"],
    "治疗": ["heal", "healing", "cure"],
    "法术": ["spell", "magic"],
    "武器": ["weapon", "arms"],
    "护甲": ["armor", "armour"],
    "骰子": ["dice", "roll"],
    "等级": ["level"],
    "经验": ["experience", "xp"],
    "线索": ["clue", "hint", "evidence"],
    "战斗": ["combat", "battle", "fight"],
    "营地": ["camp", "rest"],
    "物品": ["item", "object", "thing"],
    "门": ["door", "gate", "entrance"],
    "钥匙": ["key"],
    "宝藏": ["treasure", "loot"],
    "陷阱": ["trap", "hazard"],
    "怪物": ["monster", "creature", "beast"],
    "头目": ["boss", "leader", "chief"],
    "任务": ["quest", "mission", "task"],
    "奖励": ["reward", "prize"],
    "回合": ["turn", "round"],
    "移动": ["move", "movement"],
    "搜索": ["search", "explore", "scan"],
    "隐藏": ["hidden", "secret", "conceal"],
}


def _enrich_query(query: str) -> str:
    terms = [query]
    for chinese, english in _TTRPG_TERMS.items():
        if chinese in query:
            terms.extend(english)
    return "  ".join(dict.fromkeys(terms))


# ── lexical search ──────────────────────────────────────────────────
def _terms(text: str) -> list[str]:
    normalized = text.casefold()
    values = _LATIN_WORD.findall(normalized)
    for char in _CJK.findall(normalized):
        values.append(char)
    for index in range(len(_CJK.findall(normalized)) - 1):
        values.append("".join(_CJK.findall(normalized)[index:index+2]))
    return [v for v in values if v]


def _lexical_score(query: str, *, title: str = "", headings: str = "", keywords: str = "",
                   tags: str = "", scene_type: str = "", content: str = "") -> float:
    q = _terms(query)
    if not q:
        return 0.0
    fields = [
        (title, 4.0), (headings, 3.0), (keywords, 2.5),
        (tags, 2.0), (scene_type, 2.0), (content, 1.0),
    ]
    score = 0.0
    for field, weight in fields:
        folded = field.casefold()
        for term in q:
            score += folded.count(term) * weight
    return score / max(len(_terms(content)), 1)


# ═══════════════════════════════════════════════════════════════════
# commands
# ═══════════════════════════════════════════════════════════════════

def cmd_doctor(_args: argparse.Namespace) -> dict[str, Any]:
    _ensure_data()
    index = _load_index()
    srd_root = Path(__file__).resolve().parent.parent / "skills" / "dnd-dm" / "srd"
    return {
        "status": "ok",
        "data_dir": str(_DATA_ROOT),
        "campaign_count": len(index),
        "srd_available": srd_root.exists(),
        "srd_path": str(srd_root) if srd_root.exists() else None,
    }


def cmd_campaign_start(args: argparse.Namespace) -> dict[str, Any]:
    _ensure_data()
    index = _load_index()
    campaign_id = _slug(args.name) + "_" + uuid.uuid4().hex[:6]
    entry = {
        "id": campaign_id,
        "name": args.name,
        "edition": args.edition or "2024",
        "locale": args.locale or "en",
        "status": "active",
        "revision": 1,
    }
    index[campaign_id] = {k: v for k, v in entry.items() if k != "revision"}
    _save_index(index)
    campaign_dir = _data_dir(campaign_id)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    _write_json(campaign_dir / "campaign.json", entry)
    (campaign_dir / "characters").mkdir(exist_ok=True)
    (campaign_dir / "modules").mkdir(exist_ok=True)
    (campaign_dir / "saves").mkdir(exist_ok=True)
    _write_json(campaign_dir / "progress.json", {})
    return {"campaign_id": campaign_id, "name": args.name, "status": "created"}


def cmd_campaign_list(_args: argparse.Namespace) -> dict[str, Any]:
    index = _load_index()
    campaigns = []
    for cid, data in index.items():
        campaign_file = _data_dir(cid) / "campaign.json"
        meta = _read_json(campaign_file)
        campaigns.append({
            "id": cid,
            "name": data.get("name", meta.get("name", "")),
            "edition": data.get("edition", meta.get("edition", "")),
            "locale": data.get("locale", meta.get("locale", "")),
            "status": data.get("status", meta.get("status", "")),
        })
    return {"campaigns": campaigns}


def cmd_campaign_get(args: argparse.Namespace) -> dict[str, Any]:
    meta = _read_json(_data_dir(args.campaign) / "campaign.json")
    if not meta:
        return {"error": f"campaign not found: {args.campaign}"}
    return {"campaign": meta}


def cmd_character_create(args: argparse.Namespace) -> dict[str, Any]:
    char_dir = _data_dir(args.campaign) / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    char_file = char_dir / f"{_slug(args.name)}.json"
    if char_file.exists():
        return {"error": f"character already exists: {args.name}"}
    sheet = json.loads(args.sheet) if args.sheet else {}
    char_data = {
        "id": _slug(args.name),
        "campaign_id": args.campaign,
        "name": args.name,
        "character_type": args.type or "pc",
        "sheet": sheet,
        "revision": 1,
    }
    _write_json(char_file, char_data)
    return {"character": char_data}


def cmd_character_list(args: argparse.Namespace) -> dict[str, Any]:
    char_dir = _data_dir(args.campaign) / "characters"
    if not char_dir.exists():
        return {"characters": []}
    chars = []
    for f in sorted(char_dir.iterdir()):
        if f.suffix == ".json":
            chars.append(_read_json(f))
    return {"characters": chars}


def cmd_character_get(args: argparse.Namespace) -> dict[str, Any]:
    char_file = _data_dir(args.campaign) / "characters" / f"{_slug(args.name)}.json"
    if not char_file.exists():
        return {"error": f"character not found: {args.name}"}
    return {"character": _read_json(char_file)}


def cmd_module_ingest(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.path)
    if not source.exists():
        return {"error": f"file not found: {args.path}"}
    title = args.title or source.stem
    source_key = _slug(title)
    content = source.read_text(encoding="utf-8")
    checksum = hashlib.sha256(content.encode()).hexdigest()
    mod_dir = _data_dir(args.campaign) / "modules"
    mod_dir.mkdir(parents=True, exist_ok=True)

    # parse chapters (H1) and scenes (H2/H3)
    chapters = []
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(content))
    if not matches:
        chapters.append({"ordinal": 0, "title": title, "scenes": [{"ordinal": 0, "title": title, "line": 1}]})
    else:
        ch_matches = [m for m in matches if len(m.group(1)) == 1]
        if not ch_matches:
            ch_matches = [type("m", (), {"start": lambda: 0, "group": lambda _: title, "end": lambda: len(content)})()]
        for ci, cm in enumerate(ch_matches):
            ch_start = cm.start()
            ch_end = ch_matches[ci + 1].start() if ci + 1 < len(ch_matches) else len(content)
            ch_body = content[ch_start:ch_end]
            scenes = []
            scene_levels = [m2 for m2 in matches if ch_start < m2.start() < ch_end and len(m2.group(1)) >= 2]
            sc_level = 2
            if scene_levels:
                h2 = sum(1 for m2 in scene_levels if len(m2.group(1)) == 2)
                h3 = sum(1 for m2 in scene_levels if len(m2.group(1)) == 3)
                sc_level = 3 if h2 and h3 >= h2 * 5 else (2 if h2 else 3)
            sc_heads = [m2 for m2 in matches if ch_start < m2.start() < ch_end and len(m2.group(1)) == sc_level]
            if not sc_heads:
                scenes.append({"ordinal": 0, "title": cm.group(2).strip(), "line": content.count("\n", 0, ch_start) + 1})
            for si, sh in enumerate(sc_heads):
                scenes.append({
                    "ordinal": si,
                    "title": sh.group(2).strip(),
                    "line": content.count("\n", 0, sh.start()) + 1,
                })
            chapters.append({
                "ordinal": ci,
                "title": cm.group(2).strip() if hasattr(cm, "group") else title,
                "scenes": scenes,
            })

    module_file = mod_dir / f"{source_key}.md"
    existing_scenes = module_file.read_text(encoding="utf-8") if module_file.exists() else ""
    module_file.write_text(content, encoding="utf-8")

    scene_count = sum(len(ch["scenes"]) for ch in chapters)
    return {
        "source_key": source_key,
        "title": title,
        "chapters": len(chapters),
        "scenes": scene_count,
        "skipped": bool(existing_scenes) and existing_scenes == content,
    }


def cmd_module_index(args: argparse.Namespace) -> dict[str, Any]:
    mod_dir = _data_dir(args.campaign) / "modules"
    if not mod_dir.exists():
        return {"scenes": []}
    scenes = []
    for f in sorted(mod_dir.iterdir()):
        if f.suffix == ".md":
            content = f.read_text(encoding="utf-8")
            heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
            src_key = f.stem
            si = 0
            for match in heading_re.finditer(content):
                if len(match.group(1)) >= 2:
                    scenes.append({
                        "scene_id": _scene_id(src_key, 0, si),
                        "title": match.group(2).strip(),
                        "source_key": src_key,
                        "line": content.count("\n", 0, match.start()) + 1,
                        "module": src_key,
                    })
                    si += 1
    return {"scenes": scenes}


def _find_current_scene(campaign_id: str, scope_id: str = "party",
                        progress: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if progress is None:
        progress = _read_json(_data_dir(campaign_id) / "progress.json")
    for effective_scope in [scope_id, "party"] if scope_id != "party" else [scope_id]:
        entry = progress.get(effective_scope)
        if entry and entry.get("status") == "current":
            inherited = effective_scope != scope_id
            return {"scope_id": effective_scope, "inherited_from_party": inherited, **entry}
    return None


def cmd_module_current(args: argparse.Namespace) -> dict[str, Any]:
    progress = _read_json(_data_dir(args.campaign) / "progress.json")
    current = _find_current_scene(args.campaign, args.scope or "party", progress)
    if current is None:
        return {"scene": None}
    return {"scene": current}


def cmd_module_read_scene(args: argparse.Namespace) -> dict[str, Any]:
    scene_id = args.scene
    parts = scene_id.split("__ch")
    if len(parts) != 2:
        return {"error": f"invalid scene_id: {scene_id}"}
    mod_dir = _data_dir(args.campaign) / "modules"
    for f in mod_dir.iterdir():
        if f.suffix == ".md" and scene_id.startswith(f.stem):
            content = f.read_text(encoding="utf-8")
            return {"content": content}
    return {"error": f"scene module not found: {scene_id}"}


def cmd_module_search(args: argparse.Namespace) -> list[dict[str, Any]]:
    mod_dir = _data_dir(args.campaign) / "modules"
    if not mod_dir.exists():
        return []
    query = _enrich_query(args.query)
    q_terms = _terms(query)
    if not q_terms:
        return []
    hits = []
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
    for f in sorted(mod_dir.iterdir()):
        if f.suffix == ".md":
            content = f.read_text(encoding="utf-8")
            src_key = f.stem
            scene_heads = [(m.start(), m.group(2).strip()) for m in heading_re.finditer(content) if len(m.group(1)) >= 2]
            for si, (start, title) in enumerate(scene_heads):
                end = scene_heads[si + 1][0] if si + 1 < len(scene_heads) else len(content)
                text = content[start:end]
                score = _lexical_score(query, title=title, content=text)
                if score > 0:
                    hits.append({
                        "id": _scene_id(src_key, 0, si),
                        "score": round(score, 4),
                        "title": title,
                        "snippet": text[:200].strip(),
                        "source_key": src_key,
                    })
    hits.sort(key=lambda h: -h["score"])
    return hits[:args.limit]


def cmd_module_set_progress(args: argparse.Namespace) -> dict[str, Any]:
    progress = _read_json(_data_dir(args.campaign) / "progress.json")
    scope = args.scope or "party"
    entry = progress.setdefault(scope, {})
    entry["scene_id"] = args.scene
    entry["status"] = args.status or "current"
    if args.progress is not None:
        entry["progress"] = max(0, min(100, args.progress))
    if args.room is not None:
        entry["current_room"] = args.room
    if args.state:
        state = json.loads(args.state)
        existing = entry.get("state", {})
        if isinstance(existing, dict) and isinstance(state, dict):
            entry["state"] = {**existing, **state}
        else:
            entry["state"] = state
    entry["state_version"] = entry.get("state_version", 0) + 1

    if entry.get("status") == "current":
        for other_scope, other_entry in progress.items():
            if other_scope != scope and other_entry.get("status") == "current" and other_entry.get("scene_id") == args.scene:
                pass
        for other_scope, other_entry in progress.items():
            if other_scope != scope and other_entry.get("status") == "current":
                if other_entry.get("scene_id") != args.scene:
                    pass

    _write_json(_data_dir(args.campaign) / "progress.json", progress)
    return {"scope": scope, "scene_id": args.scene, "status": entry["status"]}


def cmd_rules_search(args: argparse.Namespace) -> list[dict[str, Any]]:
    srd_root = Path(__file__).resolve().parent.parent / "skills" / "dnd-dm" / "srd"
    query = _enrich_query(args.query)
    q_terms = _terms(query)
    if not q_terms or not srd_root.exists():
        return []
    hits = []
    for f in sorted(srd_root.rglob("*.md")):
        content = f.read_text(encoding="utf-8", errors="replace")
        score = _lexical_score(query, title=f.stem, content=content)
        if score > 0:
            hits.append({"id": f.stem, "score": round(score, 4), "source": str(f.relative_to(srd_root)), "snippet": content[:200].strip()})
    hits.sort(key=lambda h: -h["score"])
    return hits[:args.limit]


def cmd_roll_dice(args: argparse.Namespace) -> dict[str, Any]:
    import random
    expr = args.expression.strip()
    total = 0
    details = []
    pattern = re.compile(r"(\d+)[dD](\d+)(?:\+(\d+))?")
    for m in pattern.finditer(expr):
        count, sides, bonus = int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else 0
        rolls = [random.randint(1, sides) for _ in range(count)]
        subtotal = sum(rolls) + bonus
        total += subtotal
        details.append(f"{count}d{sides}({','.join(map(str,rolls))})+{bonus if bonus else 0}={subtotal}")
    return {"expression": expr, "total": total, "details": details}


def cmd_roll_check(args: argparse.Namespace) -> dict[str, Any]:
    import random
    roll = random.randint(1, 20)
    if args.advantage:
        roll2 = random.randint(1, 20)
        roll = max(roll, roll2)
    elif args.disadvantage:
        roll2 = random.randint(1, 20)
        roll = min(roll, roll2)
    bonus = args.score or 0
    if args.proficient:
        bonus += 2 + (args.level or 1) // 4
    result = roll + bonus
    return {
        "roll": roll, "bonus": bonus, "total": result,
        "dc": args.dc, "success": result >= (args.dc or 10),
        "advantage": bool(args.advantage), "disadvantage": bool(args.disadvantage),
    }


def cmd_roll_attack(args: argparse.Namespace) -> dict[str, Any]:
    import random
    roll = random.randint(1, 20)
    bonus = args.score or 0
    if args.proficient:
        bonus += 2 + (args.level or 1) // 4
    result = roll + bonus
    return {
        "roll": roll, "bonus": bonus, "total": result,
        "ac": args.dc, "hit": result >= (args.dc or 10),
    }




def cmd_event_add(args: argparse.Namespace) -> dict[str, Any]:
    events_file = _data_dir(args.campaign) / "events.jsonl"
    payload = json.loads(args.payload) if args.payload else {}
    event = {
        "id": str(uuid.uuid4()),
        "campaign_id": args.campaign,
        "type": args.type or "general",
        "summary": args.summary or "",
        "payload": payload,
    }
    _append_line(events_file, event)
    return event


def cmd_event_list(args: argparse.Namespace) -> dict[str, Any]:
    events = _json_lines(_data_dir(args.campaign) / "events.jsonl")
    return {"events": events}


def _memory_fact_key(memory_type: str, subject: str) -> str:
    """Derive a repeatable fallback key while preferring an explicit domain key."""
    kind = _slug(memory_type) or "fact"
    subject_key = _slug(subject)
    if not subject_key:
        subject_key = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{subject_key}"


def _current_memories(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the append-only revision log into current active facts."""
    legacy: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    for memory in memories:
        fact_key = memory.get("fact_key")
        if not fact_key:
            legacy.append(memory)
            continue
        latest[str(fact_key)] = memory
    return legacy + [
        memory
        for memory in latest.values()
        if memory.get("status", "active") == "active"
    ]


def cmd_memory_upsert(args: argparse.Namespace) -> dict[str, Any]:
    mem_file = _data_dir(args.campaign) / "memories.jsonl"
    memories = _json_lines(mem_file)
    fact_key = args.fact_key or _memory_fact_key(
        args.type or "fact", args.subject or ""
    )
    previous = next(
        (memory for memory in reversed(memories) if memory.get("fact_key") == fact_key),
        None,
    )
    actual_revision = int(previous.get("revision", 0)) if previous else 0
    if args.expected_revision is not None and args.expected_revision != actual_revision:
        return {
            "error": "memory revision conflict",
            "fact_key": fact_key,
            "expected_revision": args.expected_revision,
            "actual_revision": actual_revision,
        }
    content = (
        args.content
        if args.content is not None
        else (previous or {}).get("content", "")
    )
    if not content:
        return {"error": "memory content is required", "fact_key": fact_key}
    source_event_ids = (
        list(args.evidence_event)
        if args.evidence_event
        else list((previous or {}).get("source_event_ids", []))
    )
    memory = {
        "id": str(uuid.uuid4()),
        "campaign_id": args.campaign,
        "fact_key": fact_key,
        "type": args.type or (previous or {}).get("type", "fact"),
        "subject": (
            args.subject
            if args.subject is not None
            else (previous or {}).get("subject", "")
        ),
        "subject_ref": (
            args.subject_ref
            if args.subject_ref is not None
            else (previous or {}).get("subject_ref")
        ),
        "predicate": (
            args.predicate
            if args.predicate is not None
            else (previous or {}).get("predicate")
        ),
        "content": content,
        "revision": actual_revision + 1,
        "status": "active",
        "supersedes_revision_id": (previous or {}).get("id"),
        "source_event_ids": source_event_ids,
        "importance": (
            args.importance
            if args.importance is not None
            else (previous or {}).get("importance", 3)
        ),
        "disclosure_scope": (
            args.disclosure
            if args.disclosure is not None
            else (previous or {}).get("disclosure_scope", "party")
        ),
    }
    _append_line(mem_file, memory)
    return memory


def cmd_memory_list(args: argparse.Namespace) -> dict[str, Any]:
    memories = _current_memories(
        _json_lines(_data_dir(args.campaign) / "memories.jsonl")
    )
    return {"memories": memories}


def cmd_memory_search(args: argparse.Namespace) -> dict[str, Any]:
    memories = _current_memories(
        _json_lines(_data_dir(args.campaign) / "memories.jsonl")
    )
    query = _enrich_query(args.query)
    scored = []
    for m in memories:
        score = _lexical_score(
            query, title=m.get("subject", ""), content=m.get("content", "")
        )
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return {"memories": [m for _, m in scored]}


def cmd_memory_history(args: argparse.Namespace) -> dict[str, Any]:
    memories = _json_lines(_data_dir(args.campaign) / "memories.jsonl")
    return {
        "fact_key": args.fact_key,
        "revisions": [
            memory for memory in memories if memory.get("fact_key") == args.fact_key
        ],
    }


def cmd_save_create(args: argparse.Namespace) -> dict[str, Any]:
    campaign_dir = _data_dir(args.campaign) / "campaign.json"
    if not campaign_dir.exists():
        return {"error": f"campaign not found: {args.campaign}"}
    saves_dir = _data_dir(args.campaign) / "saves"
    saves_dir.mkdir(parents=True, exist_ok=True)
    slot = 1
    while (saves_dir / str(slot)).exists():
        slot += 1
    snapshot_dir = saves_dir / str(slot)
    snapshot_dir.mkdir()
    # snapshot files
    for f in _data_dir(args.campaign).iterdir():
        if f.name != "saves" and not f.name.startswith("."):
            if f.is_file():
                shutil.copy2(f, snapshot_dir / f.name)
            elif f.is_dir():
                shutil.copytree(f, snapshot_dir / f.name, dirs_exist_ok=True)
    manifest = {"slot": slot, "label": args.label or f"Save {slot}"}
    _write_json(snapshot_dir / "manifest.json", manifest)
    return manifest


def cmd_save_list(args: argparse.Namespace) -> dict[str, Any]:
    saves_dir = _data_dir(args.campaign) / "saves"
    if not saves_dir.exists():
        return {"saves": []}
    saves = []
    for d in sorted(saves_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        if d.is_dir():
            manifest = _read_json(d / "manifest.json")
            saves.append(manifest)
    return {"saves": saves}


def cmd_save_restore(args: argparse.Namespace) -> dict[str, Any]:
    slot = args.slot
    snapshot_dir = _data_dir(args.campaign) / "saves" / str(slot)
    if not snapshot_dir.exists():
        return {"error": f"save slot not found: {slot}"}
    manifest = _read_json(snapshot_dir / "manifest.json")
    campaign_dir = _data_dir(args.campaign)
    for f in snapshot_dir.iterdir():
        if f.name == "manifest.json":
            continue
        target = campaign_dir / f.name
        if f.is_file():
            shutil.copy2(f, target)
        elif f.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(f, target)
    return {"slot": slot, "label": manifest.get("label", ""), "status": "restored"}


# ═══════════════════════════════════════════════════════════════════
# CLI dispatcher
# ═══════════════════════════════════════════════════════════════════

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sagasmith-portable")
    sub = p.add_subparsers(dest="group", required=True)
    # doctor
    sub.add_parser("doctor")
    # campaign
    cp = sub.add_parser("campaign")
    csub = cp.add_subparsers(dest="action", required=True)
    cp_start = csub.add_parser("start")
    cp_start.add_argument("--name", required=True)
    cp_start.add_argument("--edition")
    cp_start.add_argument("--locale")
    csub.add_parser("list")
    cp_get = csub.add_parser("get")
    cp_get.add_argument("--campaign", required=True)
    # character
    chp = sub.add_parser("character")
    chsub = chp.add_subparsers(dest="action", required=True)
    ch_create = chsub.add_parser("create")
    ch_create.add_argument("--campaign", required=True)
    ch_create.add_argument("--name", required=True)
    ch_create.add_argument("--type")
    ch_create.add_argument("--sheet")
    ch_list = chsub.add_parser("list")
    ch_list.add_argument("--campaign", required=True)
    ch_get = chsub.add_parser("get")
    ch_get.add_argument("--campaign", required=True)
    ch_get.add_argument("--name", required=True)
    # module
    mp = sub.add_parser("module")
    msub = mp.add_subparsers(dest="action", required=True)
    mi = msub.add_parser("ingest")
    mi.add_argument("--campaign", required=True)
    mi.add_argument("--path", required=True)
    mi.add_argument("--title")
    mindex = msub.add_parser("index")
    mindex.add_argument("--campaign", required=True)
    mindex.add_argument("--limit", type=int, default=100)
    mcur = msub.add_parser("current")
    mcur.add_argument("--campaign", required=True)
    mcur.add_argument("--scope", default="party")
    mrs = msub.add_parser("read-scene")
    mrs.add_argument("--campaign", required=True)
    mrs.add_argument("--scene", required=True)
    msr = msub.add_parser("search")
    msr.add_argument("--campaign", required=True)
    msr.add_argument("--query", required=True)
    msr.add_argument("--limit", type=int, default=8)
    msp = msub.add_parser("set-progress")
    msp.add_argument("--campaign", required=True)
    msp.add_argument("--scene", required=True)
    msp.add_argument("--scope", default="party")
    msp.add_argument("--progress", type=int)
    msp.add_argument("--room")
    msp.add_argument("--state")
    msp.add_argument("--status")
    # rules
    rp = sub.add_parser("rules")
    rsub = rp.add_subparsers(dest="action", required=True)
    rs = rsub.add_parser("search")
    rs.add_argument("--campaign", required=True)
    rs.add_argument("--query", required=True)
    rs.add_argument("--limit", type=int, default=5)
    # roll
    rlp = sub.add_parser("roll")
    rlsub = rlp.add_subparsers(dest="action", required=True)
    rd = rlsub.add_parser("dice")
    rd.add_argument("--expression", required=True)
    rc = rlsub.add_parser("check")
    rc.add_argument("--dc", type=int, default=10)
    rc.add_argument("--score", type=int)
    rc.add_argument("--advantage", action="store_true")
    rc.add_argument("--disadvantage", action="store_true")
    rc.add_argument("--proficient", action="store_true")
    rc.add_argument("--level", type=int)
    ra = rlsub.add_parser("attack")
    ra.add_argument("--dc", type=int, default=10)
    ra.add_argument("--score", type=int)
    ra.add_argument("--proficient", action="store_true")
    ra.add_argument("--level", type=int)
    # event
    ep = sub.add_parser("event")
    esub = ep.add_subparsers(dest="action", required=True)
    ea = esub.add_parser("add")
    ea.add_argument("--campaign", required=True)
    ea.add_argument("--type", default="general")
    ea.add_argument("--summary")
    ea.add_argument("--payload")
    el = esub.add_parser("list")
    el.add_argument("--campaign", required=True)
    # memory
    memp = sub.add_parser("memory")
    memsub = memp.add_subparsers(dest="action", required=True)
    memory_writers = [memsub.add_parser("add"), memsub.add_parser("upsert")]
    for memory_writer in memory_writers:
        memory_writer.add_argument("--campaign", required=True)
        memory_writer.add_argument("--fact-key")
        memory_writer.add_argument("--type")
        memory_writer.add_argument("--subject")
        memory_writer.add_argument("--subject-ref")
        memory_writer.add_argument("--predicate")
        memory_writer.add_argument("--content")
        memory_writer.add_argument("--expected-revision", type=int)
        memory_writer.add_argument("--evidence-event", action="append", default=[])
        memory_writer.add_argument("--importance", type=int, choices=range(1, 6))
        memory_writer.add_argument(
            "--disclosure", choices=("keeper", "party", "public")
        )
    ml = memsub.add_parser("list")
    ml.add_argument("--campaign", required=True)
    mms = memsub.add_parser("search")
    mms.add_argument("--campaign", required=True)
    mms.add_argument("--query", required=True)
    mh = memsub.add_parser("history")
    mh.add_argument("--campaign", required=True)
    mh.add_argument("--fact-key", required=True)
    # save
    svp = sub.add_parser("save")
    svsub = svp.add_subparsers(dest="action", required=True)
    svc = svsub.add_parser("create")
    svc.add_argument("--campaign", required=True)
    svc.add_argument("--label")
    svl = svsub.add_parser("list")
    svl.add_argument("--campaign", required=True)
    svr = svsub.add_parser("restore")
    svr.add_argument("--campaign", required=True)
    svr.add_argument("--slot", type=int, required=True)

    json_subcommands = [
        mcur, mrs, msr, msp, mindex, mi, ch_create, ch_list, ch_get, cp_get,
        ea, el, *memory_writers, ml, mms, mh, svc, svl, svr, rp, rs, cp_start,
    ]
    for subp in json_subcommands:
        subp.add_argument("--json", action="store_true", default=True)

    return p


CMD_MAP: dict[str, dict[str, Any]] = {
    ("doctor", None): cmd_doctor,
    ("campaign", "start"): cmd_campaign_start,
    ("campaign", "list"): cmd_campaign_list,
    ("campaign", "get"): cmd_campaign_get,
    ("character", "create"): cmd_character_create,
    ("character", "list"): cmd_character_list,
    ("character", "get"): cmd_character_get,
    ("module", "ingest"): cmd_module_ingest,
    ("module", "index"): cmd_module_index,
    ("module", "current"): cmd_module_current,
    ("module", "read-scene"): cmd_module_read_scene,
    ("module", "search"): cmd_module_search,
    ("module", "set-progress"): cmd_module_set_progress,
    ("rules", "search"): cmd_rules_search,
    ("roll", "dice"): cmd_roll_dice,
    ("roll", "check"): cmd_roll_check,
    ("roll", "attack"): cmd_roll_attack,
    ("event", "add"): cmd_event_add,
    ("event", "list"): cmd_event_list,
    ("memory", "add"): cmd_memory_upsert,
    ("memory", "upsert"): cmd_memory_upsert,
    ("memory", "list"): cmd_memory_list,
    ("memory", "search"): cmd_memory_search,
    ("memory", "history"): cmd_memory_history,
    ("save", "create"): cmd_save_create,
    ("save", "list"): cmd_save_list,
    ("save", "restore"): cmd_save_restore,
}


def main() -> None:
    args = _parser().parse_args()
    key = (args.group, args.action) if hasattr(args, "action") and args.action else (args.group, None)
    handler = CMD_MAP.get(key)
    if handler is None:
        _parser().print_help()
        return
    result = handler(args)
    if isinstance(result, list):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
